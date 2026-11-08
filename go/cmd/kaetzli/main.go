// kaetzli starts and manages kaetzli virtual machines without a checkout of the
// repository: one file, and qemu (docs/proposals/launcher.md). It is tools/vm.sh's
// commands and defaults, with images from the latest snapshot release.
//
//	kaetzli start [NAME] [--ext a,b | --no-ext] [--mem MB] [--cpus N] [--size 8G]
//	              [--image DIR | --snapshot NAME|latest] [--secure-boot | --no-secure-boot]
//	kaetzli ssh [--root] [NAME] [command...]
//	kaetzli console [NAME]           the serial console (Ctrl-] to leave)
//	kaetzli stop [NAME] [--force]
//	kaetzli list | info [NAME] | update | rollback [NAME] | rm [NAME] [--yes] | version
//
// A machine is a directory under the platform's data directory: its /var disk and machine
// ID, an ssh key made once, its remembered options and this boot's console log. Images are
// snapshots, cached once each, verified against SHA256SUMS before anything boots them.
//
// Updates roll back (go/internal/rollout): a machine remembers the last snapshot it came
// up on, and one that does not come up (no ssh before the timeout, or qemu exits) is
// marked bad for that machine and the machine is started again on the last good one.
// `update` keeps the newest two and whatever a machine needs; `rollback` pins the one
// before.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/fwilhe2/kaetzli/go/internal/qemu"
	"github.com/fwilhe2/kaetzli/go/internal/rollout"
	"github.com/fwilhe2/kaetzli/go/internal/snapshot"
)

// Set at build time (-ldflags -X main.version=...), to the snapshot it is released with.
var version = "dev"

const defaultName = "kaetzli"

var nameRE = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9-]{0,62}$`)

type config struct {
	Arch          string   `json:"arch"`
	Exts          []string `json:"exts"`
	Mem           string   `json:"mem"`
	CPUs          string   `json:"cpus"`
	Image         string   `json:"image"`                 // "" for snapshots, else a directory
	SecureBoot    bool     `json:"secure_boot,omitempty"` // the UKI, its digest alone in db (secureboot.go)
	rollout.State          // which snapshot: pinned, last good, failed here
}

type ports struct {
	SSH     int    `json:"ssh"`
	Console int    `json:"console"`
	QMP     int    `json:"qmp"`
	Image   string `json:"image"` // what this boot booted
}

func die(format string, a ...any) {
	fmt.Fprintf(os.Stderr, "error: "+format+"\n", a...)
	os.Exit(1)
}

func say(format string, a ...any) { fmt.Printf(">> "+format+"\n", a...) }

func arch() string {
	switch runtime.GOARCH {
	case "amd64", "arm64":
		return runtime.GOARCH
	}
	die("unsupported architecture %s (amd64 or arm64)", runtime.GOARCH)
	return ""
}

// dataDir is where machines live: XDG's data directory on Linux, the platform's
// configuration directory elsewhere (~/Library/Application Support, %AppData%).
func dataDir() string {
	if d := os.Getenv("KAETZLI_HOME"); d != "" {
		return d
	}
	if runtime.GOOS == "linux" {
		if d := os.Getenv("XDG_DATA_HOME"); d != "" {
			return filepath.Join(d, "kaetzli")
		}
		h, err := os.UserHomeDir()
		if err != nil {
			die("%v", err)
		}
		return filepath.Join(h, ".local", "share", "kaetzli")
	}
	d, err := os.UserConfigDir()
	if err != nil {
		die("%v", err)
	}
	return filepath.Join(d, "kaetzli")
}

func cacheDir() string {
	if d := os.Getenv("KAETZLI_CACHE"); d != "" {
		return d
	}
	d, err := os.UserCacheDir()
	if err != nil {
		die("%v", err)
	}
	return filepath.Join(d, "kaetzli", "snapshots")
}

func releases() *snapshot.Client {
	if u := os.Getenv("KAETZLI_RELEASES"); u != "" {
		return snapshot.New(u)
	}
	return snapshot.New(snapshot.Releases)
}

func machineDir(name string) string { return filepath.Join(dataDir(), "machines", name) }

func readJSON(path string, v any) error {
	b, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return json.Unmarshal(b, v)
}

func writeJSON(path string, v any) error {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(b, '\n'), 0o644)
}

func exists(name string) bool {
	_, err := os.Stat(filepath.Join(machineDir(name), "config.json"))
	return err == nil
}

// parseSize reads 8G, 512M, 2048K or bytes.
func parseSize(s string) (int64, error) {
	mult := int64(1)
	switch {
	case strings.HasSuffix(s, "T"):
		mult, s = 1<<40, strings.TrimSuffix(s, "T")
	case strings.HasSuffix(s, "G"):
		mult, s = 1<<30, strings.TrimSuffix(s, "G")
	case strings.HasSuffix(s, "M"):
		mult, s = 1<<20, strings.TrimSuffix(s, "M")
	case strings.HasSuffix(s, "K"):
		mult, s = 1<<10, strings.TrimSuffix(s, "K")
	}
	n, err := strconv.ParseInt(s, 10, 64)
	if err != nil || n <= 0 {
		return 0, fmt.Errorf("not a size: %q", s)
	}
	return n * mult, nil
}

func freePort() int {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		die("no free port on 127.0.0.1: %v", err)
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port
}

// image is the directory a machine boots and, for a snapshot, its name: the one
// rollout.Choose picks from the cache (fetching the newest when there is none yet, and a
// pin that is not cached), or the directory it was given.
func image(c config) (string, string) {
	if c.Image != "" {
		return c.Image, ""
	}
	if c.Pin != "" && !contains(snapshot.Cached(cacheDir(), c.Arch), c.Pin) {
		say("fetching snapshot %s (%s)", c.Pin, c.Arch)
		if _, err := releases().Fetch(cacheDir(), c.Pin, c.Arch, os.Stdout); err != nil {
			die("%v", err)
		}
	}
	cached := snapshot.Cached(cacheDir(), c.Arch)
	latest, err := releases().Latest()
	if err == nil && latest != "" && (len(cached) == 0 || latest > cached[0]) {
		if len(cached) > 0 {
			say("snapshot %s is newer than %s: `kaetzli update` fetches it", latest, cached[0])
		} else {
			say("fetching snapshot %s (%s)", latest, c.Arch)
			if _, ferr := releases().Fetch(cacheDir(), latest, c.Arch, os.Stdout); ferr != nil {
				die("%v", ferr)
			}
			cached = snapshot.Cached(cacheDir(), c.Arch)
		}
	}
	if len(cached) == 0 {
		if err != nil {
			die("no snapshot cached, and the releases cannot be reached: %v", err)
		}
		die("no snapshot is published yet; boot a local build with --image DIR")
	}
	name, cerr := rollout.Choose(cached, c.State)
	if cerr != nil {
		die("%v", cerr)
	}
	return snapDir(name, c.Arch), name
}

func snapDir(name, arch string) string { return filepath.Join(cacheDir(), name+"-"+arch) }

// sshConfig is the machine's ssh_config, as test/qemu-lib.sh writes it.
func sshConfig(dir string, port int) error {
	key, _ := filepath.Abs(filepath.Join(dir, "ssh", "id_ed25519"))
	cfg := fmt.Sprintf("Host kaetzli\n    HostName 127.0.0.1\n    Port %d\n    User root\n"+
		"    IdentityFile %s\n    IdentitiesOnly yes\n    BatchMode yes\n    StrictHostKeyChecking no\n"+
		"    UserKnownHostsFile /dev/null\n    LogLevel ERROR\n", port, key)
	return os.WriteFile(filepath.Join(dir, "ssh", "config"), []byte(cfg), 0o600)
}

// machine is the qemu machine for a start; the ports are this boot's.
func machine(name string, c config, img string, p ports) qemu.Machine {
	dir, _ := filepath.Abs(machineDir(name))
	pubFile := filepath.Join(dir, "ssh", "id_ed25519.pub")
	pub, err := os.ReadFile(pubFile)
	if err != nil {
		die("%v", err)
	}
	return qemu.Machine{
		Arch: c.Arch, KVM: kvm(), Mem: c.Mem, CPUs: c.CPUs,
		Kernel: filepath.Join(img, "bzImage"), Rootfs: filepath.Join(img, "rootfs.erofs"),
		Cmdline: filepath.Join(img, "rootfs.cmdline"), Init: "/usr/lib/systemd/systemd",
		VarDisk: filepath.Join(dir, "var.img"), Sysexts: c.Exts, ExtDir: img,
		SSHPort: p.SSH, PubKey: pub, PubKeyFile: pubFile, Hostname: name, Reboot: true,
		Display: display(p.Console, filepath.Join(dir, "console.log")),
	}
}

// display replaces -nographic: the console is a TCP socket on localhost that logs
// everything to console.log, and `kaetzli console` attaches to it.
func display(port int, log string) []string {
	return []string{
		"-display", "none", "-monitor", "none",
		"-chardev", fmt.Sprintf("socket,id=con,host=127.0.0.1,port=%d,server=on,wait=off,logfile=%s", port, log),
		"-serial", "chardev:con",
	}
}

func qmpArgs(port int) []string {
	return []string{"-qmp", fmt.Sprintf("tcp:127.0.0.1:%d,server=on,wait=off", port)}
}

func kvm() bool {
	f, err := os.OpenFile("/dev/kvm", os.O_RDWR, 0)
	if err != nil {
		return false
	}
	f.Close()
	return true
}

func sshTo(dir, login string, args ...string) *exec.Cmd {
	cmd := exec.Command("ssh", append([]string{"-F", filepath.Join(dir, "ssh", "config"), "-o", "ConnectTimeout=5",
		"-l", login, "kaetzli"}, args...)...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	return cmd
}

func cmdStart(args []string) {
	name, rest := nameArg(args)
	var exts []string
	setExts, imageArg, snapArg, mem, cpus, size := false, "", "", "", "", "8G"
	secureBoot := ""
	for i := 0; i < len(rest); i++ {
		next := func() string {
			if i+1 >= len(rest) {
				die("%s needs a value", rest[i])
			}
			i++
			return rest[i]
		}
		switch rest[i] {
		case "--ext":
			setExts = true
			for _, e := range strings.Split(next(), ",") {
				if !nameRE.MatchString(e) {
					die("'%s' is not an extension name", e)
				}
				if !contains(exts, e) {
					exts = append(exts, e)
				}
			}
		case "--no-ext":
			setExts, exts = true, nil
		case "--image":
			imageArg = next()
		case "--snapshot":
			snapArg = next()
			if snapArg != "latest" && !snapshot.IsName(snapArg) {
				die("--snapshot %s: not a snapshot name (YYYYMMDD.HHMMSS-N) or latest", snapArg)
			}
		case "--mem":
			mem = next()
		case "--cpus":
			cpus = next()
		case "--size":
			size = next()
		case "--secure-boot":
			secureBoot = "on"
		case "--no-secure-boot":
			secureBoot = "off"
		default:
			die("unknown option '%s' (kaetzli help)", rest[i])
		}
	}
	if imageArg != "" && snapArg != "" {
		die("--image and --snapshot are two different things to boot; give one")
	}
	for _, n := range []string{mem, cpus} {
		if _, err := strconv.Atoi(n); n != "" && err != nil {
			die("--mem and --cpus are numbers: '%s'", n)
		}
	}
	dir := machineDir(name)
	if !exists(name) {
		if _, err := os.Stat(dir); err == nil {
			die("%s exists but is not a machine; move it out of the way", dir)
		}
		create(name, size)
	} else if pid, ok := running(dir); ok {
		if !setExts && imageArg == "" && snapArg == "" && mem == "" && cpus == "" && secureBoot == "" {
			var p ports
			_ = readJSON(filepath.Join(dir, "ports.json"), &p)
			say("'%s' is running already (pid %d, ssh 127.0.0.1:%d): kaetzli ssh%s", name, pid, p.SSH, hint(name))
			return
		}
		die("'%s' is running; stop it first (kaetzli stop%s)", name, hint(name))
	}
	var c config
	if err := readJSON(filepath.Join(dir, "config.json"), &c); err != nil {
		die("%v", err)
	}
	if setExts {
		c.Exts = exts
	}
	if imageArg != "" {
		abs, err := filepath.Abs(imageArg)
		if err != nil || !isFile(filepath.Join(abs, "rootfs.erofs")) {
			die("--image %s: no rootfs.erofs there", imageArg)
		}
		c.Image = abs
	}
	if snapArg != "" {
		c.Image, c.Pin = "", ""
		if snapArg != "latest" {
			c.Pin = snapArg
		}
	}
	if mem != "" {
		c.Mem = mem
	}
	if cpus != "" {
		c.CPUs = cpus
	}
	if secureBoot != "" {
		c.SecureBoot = secureBoot == "on"
	}
	if c.SecureBoot && c.Arch != "amd64" {
		die("--secure-boot is amd64 only for now") // before anything is remembered
	}
	img, snap := image(c)
	if c.Image != "" {
		// A copy, as tools/vm.sh makes: qemu reads the root for as long as the guest runs,
		// and a rebuild writing over the file under it would fail dm-verity's reads.
		// Snapshots in the cache are never written again, so they boot in place.
		copied, err := syncImage(c.Image, filepath.Join(dir, "image"))
		if err != nil {
			die("copying the image: %v", err)
		}
		img = copied
	}
	cfgPath := filepath.Join(dir, "config.json")
	log := filepath.Join(dir, "console.log")
	for first := true; ; first = false {
		p := ports{SSH: freePort(), Console: freePort(), QMP: freePort(), Image: img}
		m := machine(name, c, img, p)
		if c.SecureBoot {
			sb, err := prepareSecureBoot(dir, img, m, c.Image == "")
			if err != nil {
				die("--secure-boot: %v", err)
			}
			m.SecureBoot = sb
		}
		argv, err := m.Argv()
		if err != nil {
			die("%v", err)
		}
		if _, err := exec.LookPath(m.Binary()); err != nil {
			die("%s not found: install qemu", m.Binary())
		}
		if !m.KVM && first {
			fmt.Println("note: /dev/kvm not available, emulating (slow)")
		}
		// Remembered once they are known to make a command line: a typo must not stick.
		if err := writeJSON(cfgPath, c); err != nil {
			die("%v", err)
		}
		if err := sshConfig(dir, p.SSH); err != nil {
			die("%v", err)
		}
		if isFile(log) {
			_ = os.Rename(log, log+".1")
		}
		if err := writeJSON(filepath.Join(dir, "ports.json"), p); err != nil {
			die("%v", err)
		}
		pid, err := detach(m.Binary(), append(argv, qmpArgs(p.QMP)...), filepath.Join(dir, "qemu.log"))
		if err != nil {
			die("could not start %s: %v", m.Binary(), err)
		}
		_ = os.WriteFile(filepath.Join(dir, "qemu.pid"), []byte(strconv.Itoa(pid)+"\n"), 0o644)
		sbNote := ""
		if c.SecureBoot {
			sbNote = ", Secure Boot"
		}
		say("'%s' started (pid %d, %s MB, %s CPUs, extensions: %s, image: %s%s)", name, pid, c.Mem, c.CPUs,
			orNone(strings.Join(c.Exts, " ")), describe(img), sbNote)

		werr := waitUp(dir, p.SSH, log)
		if werr == nil {
			if snap != "" {
				c.State = c.Up(snap)
				_ = writeJSON(cfgPath, c)
			}
			break
		}
		tail(log, 40)
		if snap == "" {
			die("%v; the console is above (%s)", werr, log)
		}
		c.State = c.Down(snap)
		_ = writeJSON(cfgPath, c)
		to, ok := rollout.Revert(c.State, snap, snapshot.Cached(cacheDir(), c.Arch))
		if !ok {
			die("%v; the console is above (%s). Snapshot %s is marked bad for '%s'", werr, log, snap, name)
		}
		if pid, ok := running(dir); ok {
			killAndWait(pid)
		}
		_ = os.Remove(filepath.Join(dir, "qemu.pid"))
		say("snapshot %s did not come up (%v; its console is %s.1): marked bad for '%s', back to %s, its last good one",
			snap, werr, log, name, to)
		img, snap = snapDir(to, c.Arch), to
	}
	h := hint(name)
	fmt.Printf("   kaetzli ssh%s [--root]   a shell (or: ssh -F %s kaetzli)\n", h, filepath.Join(dir, "ssh", "config"))
	fmt.Printf("   kaetzli console%s        the serial console (Ctrl-] to leave)\n", h)
	fmt.Printf("   kaetzli stop%s\n", h)
}

// waitUp waits for the guest's sshd to let root in: nil once it does, else why not. A
// kernel panic on the console ends the wait at once: qemu outlives it and its port
// forward still accepts, so a probe would only ever time out.
func waitUp(dir string, port int, log string) error {
	timeout := 300 * time.Second
	if t, err := strconv.Atoi(os.Getenv("TIMEOUT")); err == nil {
		timeout = time.Duration(t) * time.Second
	}
	until := time.Now().Add(timeout)
	fmt.Printf(">> waiting for ssh on 127.0.0.1:%d ", port)
	for {
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		probe := sshTo(dir, "root", "true")
		probe = exec.CommandContext(ctx, probe.Path, probe.Args[1:]...) // ConnectTimeout does not bound the banner wait
		err := probe.Run()
		cancel()
		if err == nil {
			fmt.Println(" up")
			return nil
		}
		if _, ok := running(dir); !ok {
			fmt.Println()
			return errors.New("qemu exited")
		}
		if b, _ := os.ReadFile(log); qemu.Panicked(b) {
			fmt.Println()
			return errors.New("the guest's kernel panicked")
		}
		if time.Now().After(until) {
			fmt.Println()
			return fmt.Errorf("no ssh after %s", timeout)
		}
		fmt.Print(".")
		time.Sleep(2 * time.Second)
	}
}

// syncImage makes dst a copy of the bootable files of src, copying only what changed
// (by size and modification time) and dropping what src no longer has.
func syncImage(src, dst string) (string, error) {
	want := map[string]bool{}
	for _, f := range []string{"rootfs.erofs", "rootfs.cmdline", "bzImage"} {
		want[f] = true
	}
	if isFile(filepath.Join(src, "kaetzli.efi")) {
		want["kaetzli.efi"] = true // the unsigned UKI, for --secure-boot
	}
	for _, d := range []string{"extensions", "confexts"} {
		entries, _ := os.ReadDir(filepath.Join(src, d))
		for _, e := range entries {
			if e.Type().IsRegular() {
				want[d+"/"+e.Name()] = true
			}
		}
	}
	for f := range want {
		s, err := os.Stat(filepath.Join(src, f))
		if err != nil {
			return "", err
		}
		target := filepath.Join(dst, f)
		if d, err := os.Stat(target); err == nil && d.Size() == s.Size() && d.ModTime().Equal(s.ModTime()) {
			continue
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return "", err
		}
		if err := copyFile(filepath.Join(src, f), target+".tmp"); err != nil {
			return "", err
		}
		if err := os.Chtimes(target+".tmp", s.ModTime(), s.ModTime()); err != nil {
			return "", err
		}
		if err := os.Rename(target+".tmp", target); err != nil {
			return "", err
		}
	}
	for _, d := range []string{"extensions", "confexts"} {
		entries, _ := os.ReadDir(filepath.Join(dst, d))
		for _, e := range entries {
			if !want[d+"/"+e.Name()] {
				_ = os.Remove(filepath.Join(dst, d, e.Name()))
			}
		}
	}
	return dst, nil
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		return err
	}
	return out.Close()
}

func create(name, size string) {
	if !nameRE.MatchString(name) {
		die("'%s' is not a machine name (letters, digits and -)", name)
	}
	n, err := parseSize(size)
	if err != nil {
		die("--size: %v", err)
	}
	dir := machineDir(name)
	if err := os.MkdirAll(filepath.Join(dir, "ssh"), 0o700); err != nil {
		die("%v", err)
	}
	f, err := os.Create(filepath.Join(dir, "var.img"))
	if err != nil {
		die("%v", err)
	}
	if err := f.Truncate(n); err != nil {
		die("%v", err)
	}
	f.Close()
	id := make([]byte, 16)
	if r, err := os.Open("/dev/urandom"); err == nil {
		_, _ = io.ReadFull(r, id)
		r.Close()
	}
	_ = os.WriteFile(filepath.Join(dir, "var.img.machine-id"), []byte(fmt.Sprintf("%x", id)), 0o644)
	host, _ := os.Hostname()
	keygen := exec.Command("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f",
		filepath.Join(dir, "ssh", "id_ed25519"), "-C", "kaetzli-"+name+"@"+host)
	keygen.Stderr = os.Stderr
	if err := keygen.Run(); err != nil {
		os.RemoveAll(dir)
		die("ssh-keygen: %v (install an OpenSSH client)", err)
	}
	if err := writeJSON(filepath.Join(dir, "config.json"), config{Arch: arch(), Mem: "2048", CPUs: "2"}); err != nil {
		die("%v", err)
	}
	say("created '%s' (/var: %s, sparse) in %s", name, size, dir)
}

func cmdStop(args []string) {
	force := false
	var rest []string
	for _, a := range args {
		if a == "--force" {
			force = true
		} else {
			rest = append(rest, a)
		}
	}
	name, extra := nameArg(rest)
	if len(extra) > 0 {
		die("unknown option '%s'", extra[0])
	}
	dir := machineDir(name)
	mustExist(name)
	pid, ok := running(dir)
	if !ok {
		say("'%s' is not running", name)
		return
	}
	if !force {
		say("powering off '%s'", name)
		off := sshTo(dir, "root", "systemctl", "poweroff")
		off.Stdin, off.Stdout, off.Stderr = nil, nil, nil
		if off.Run() != nil {
			var p ports
			_ = readJSON(filepath.Join(dir, "ports.json"), &p)
			qmp(p.QMP, "system_powerdown")
		}
		until := time.Now().Add(90 * time.Second)
		for alive(pid) && time.Now().Before(until) {
			time.Sleep(time.Second)
		}
	}
	if alive(pid) {
		if !force {
			fmt.Fprintf(os.Stderr, "warning: '%s' did not power off in 90s; killing qemu\n", name)
		}
		kill(pid)
		for alive(pid) {
			time.Sleep(200 * time.Millisecond)
		}
		_ = os.Remove(filepath.Join(dir, "qemu.pid"))
		say("'%s' killed; ext4's journal puts /var right at the next boot", name)
		return
	}
	_ = os.Remove(filepath.Join(dir, "qemu.pid"))
	say("'%s' stopped", name)
}

// qmp sends one command to qemu's monitor, after the handshake it requires.
func qmp(port int, command string) {
	conn, err := net.DialTimeout("tcp", fmt.Sprintf("127.0.0.1:%d", port), 5*time.Second)
	if err != nil {
		return
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(5 * time.Second))
	r := bufio.NewReader(conn)
	_, _ = r.ReadString('\n') // the greeting
	fmt.Fprintf(conn, "{\"execute\":\"qmp_capabilities\"}\n")
	_, _ = r.ReadString('\n')
	fmt.Fprintf(conn, "{\"execute\":%q}\n", command)
	_, _ = r.ReadString('\n')
}

func cmdSSH(args []string) {
	login := "user"
	if len(args) > 0 && args[0] == "--root" {
		login, args = "root", args[1:]
	}
	name := defaultName
	if len(args) > 0 && exists(args[0]) {
		name, args = args[0], args[1:]
	}
	if len(args) > 0 && args[0] == "--root" {
		login, args = "root", args[1:]
	}
	dir := machineDir(name)
	mustExist(name)
	if _, ok := running(dir); !ok {
		die("'%s' is not running (kaetzli start%s)", name, hint(name))
	}
	cmd := sshTo(dir, login, args...)
	if err := cmd.Run(); err != nil {
		var e *exec.ExitError
		if errors.As(err, &e) {
			os.Exit(e.ExitCode())
		}
		die("ssh: %v", err)
	}
}

func cmdConsole(args []string) {
	name, extra := nameArg(args)
	if len(extra) > 0 {
		die("unknown option '%s'", extra[0])
	}
	dir := machineDir(name)
	mustExist(name)
	if _, ok := running(dir); !ok {
		die("'%s' is not running (kaetzli start%s)", name, hint(name))
	}
	var p ports
	if err := readJSON(filepath.Join(dir, "ports.json"), &p); err != nil {
		die("%v", err)
	}
	conn, err := net.Dial("tcp", fmt.Sprintf("127.0.0.1:%d", p.Console))
	if err != nil {
		die("the console: %v", err)
	}
	defer conn.Close()
	fmt.Println(">> the console of '" + name + "' (Ctrl-] to leave); press Enter for a prompt")
	restore := rawTerminal()
	defer restore()
	done := make(chan struct{})
	var once sync.Once
	finish := func() { once.Do(func() { close(done) }) }
	go func() { _, _ = io.Copy(os.Stdout, conn); finish() }()
	go func() {
		buf := make([]byte, 256)
		for {
			n, err := os.Stdin.Read(buf)
			if err != nil {
				finish()
				return
			}
			if i := strings.IndexByte(string(buf[:n]), 0x1d); i >= 0 {
				_, _ = conn.Write(buf[:i])
				conn.Close()
				return
			}
			if _, err := conn.Write(buf[:n]); err != nil {
				return
			}
		}
	}()
	<-done
	restore()
	fmt.Println()
}

func cmdList() {
	entries, _ := os.ReadDir(filepath.Join(dataDir(), "machines"))
	var names []string
	for _, e := range entries {
		if exists(e.Name()) {
			names = append(names, e.Name())
		}
	}
	sort.Strings(names)
	if len(names) == 0 {
		fmt.Println("no machines yet: kaetzli start")
		return
	}
	fmt.Printf("%-16s %-8s %-6s %-28s %s\n", "NAME", "STATE", "SSH", "IMAGE", "EXTENSIONS")
	for _, n := range names {
		dir := machineDir(n)
		var c config
		var p ports
		_ = readJSON(filepath.Join(dir, "config.json"), &c)
		state, port, img := "stopped", "-", describe(c.Image)
		if _, ok := running(dir); ok {
			_ = readJSON(filepath.Join(dir, "ports.json"), &p)
			state, port = "running", strconv.Itoa(p.SSH)
			if c.Image == "" {
				img = describe(p.Image)
			}
		}
		fmt.Printf("%-16s %-8s %-6s %-28s %s\n", n, state, port, img, orNone(strings.Join(c.Exts, ",")))
	}
}

func cmdInfo(args []string) {
	name, _ := nameArg(args)
	mustExist(name)
	dir := machineDir(name)
	var c config
	_ = readJSON(filepath.Join(dir, "config.json"), &c)
	fmt.Printf("machine     %s (%s)\n", name, dir)
	fmt.Printf("arch        %s, %s MB, %s CPUs\n", c.Arch, c.Mem, c.CPUs)
	fmt.Printf("extensions  %s\n", orNone(strings.Join(c.Exts, " ")))
	if c.SecureBoot {
		fmt.Printf("secure boot on: the firmware boots only this start's UKI (its digest in db)\n")
	}
	if pid, ok := running(dir); ok {
		var p ports
		_ = readJSON(filepath.Join(dir, "ports.json"), &p)
		fmt.Printf("state       running (pid %d), ssh 127.0.0.1:%d, console 127.0.0.1:%d\n", pid, p.SSH, p.Console)
		fmt.Printf("image       %s\n", describe(p.Image))
	} else {
		fmt.Printf("state       stopped\n")
		fmt.Printf("image       %s\n", describe(c.Image))
	}
	if c.Image == "" {
		if cached := snapshot.Cached(cacheDir(), c.Arch); len(cached) > 0 {
			fmt.Printf("newest      %s, cached\n", cached[0])
		}
		if c.Pin != "" {
			fmt.Printf("pinned to   %s (kaetzli start%s --snapshot latest unpins)\n", c.Pin, hint(name))
		}
		if c.Good != "" {
			fmt.Printf("last good   %s\n", c.Good)
		}
		if len(c.Bad) > 0 {
			fmt.Printf("failed here %s (skipped)\n", strings.Join(c.Bad, " "))
		}
	}
}

func cmdUpdate() {
	a := arch()
	latest, err := releases().Latest()
	if err != nil {
		die("the releases cannot be reached: %v", err)
	}
	if latest == "" {
		die("no snapshot is published yet")
	}
	cached := snapshot.Cached(cacheDir(), a)
	if len(cached) > 0 && cached[0] == latest {
		say("snapshot %s is the newest, and cached", latest)
	} else {
		say("fetching snapshot %s (%s)", latest, a)
		if _, err := releases().Fetch(cacheDir(), latest, a, os.Stdout); err != nil {
			die("%v", err)
		}
		say("verified and cached; a machine boots it at its next start (kaetzli stop, then start),")
		say("and goes back to the snapshot it last came up on if this one does not come up")
	}
	// Older snapshots go, but for the one before the newest (a way back), and any a machine
	// is pinned to, last came up on, or is running now (rollout.Keep).
	var states []rollout.State
	var inUse []string
	entries, _ := os.ReadDir(filepath.Join(dataDir(), "machines"))
	for _, e := range entries {
		dir := machineDir(e.Name())
		var c config
		if readJSON(filepath.Join(dir, "config.json"), &c) == nil && c.Arch == a {
			states = append(states, c.State)
		}
		var p ports
		if _, ok := running(dir); ok && readJSON(filepath.Join(dir, "ports.json"), &p) == nil {
			inUse = append(inUse, strings.TrimSuffix(filepath.Base(p.Image), "-"+a))
		}
	}
	cached = snapshot.Cached(cacheDir(), a)
	keep := rollout.Keep(cached, states, inUse)
	for _, old := range cached {
		if !contains(keep, old) {
			_ = os.RemoveAll(snapDir(old, a))
			say("removed snapshot %s from the cache", old)
		}
	}
}

// cmdRollback pins a machine to the cached snapshot before the one it boots now.
func cmdRollback(args []string) {
	name, extra := nameArg(args)
	if len(extra) > 0 {
		die("unknown option '%s'", extra[0])
	}
	mustExist(name)
	dir := machineDir(name)
	var c config
	if err := readJSON(filepath.Join(dir, "config.json"), &c); err != nil {
		die("%v", err)
	}
	if c.Image != "" {
		die("'%s' boots %s, not a snapshot", name, c.Image)
	}
	cached := snapshot.Cached(cacheDir(), c.Arch)
	current, err := rollout.Choose(cached, c.State)
	if err != nil {
		die("%v", err)
	}
	prev := rollout.Previous(cached, current)
	if prev == "" {
		die("'%s' boots %s and no older snapshot is cached", name, current)
	}
	c.Pin = prev
	if err := writeJSON(filepath.Join(dir, "config.json"), c); err != nil {
		die("%v", err)
	}
	say("'%s' is pinned to snapshot %s (was %s); it boots it at its next start", name, prev, current)
	if _, ok := running(dir); ok {
		say("it is running now: kaetzli stop%s, then kaetzli start%s", hint(name), hint(name))
	}
	say("back to the newest: kaetzli start%s --snapshot latest", hint(name))
}

func cmdRm(args []string) {
	yes := false
	var rest []string
	for _, a := range args {
		if a == "--yes" || a == "-y" {
			yes = true
		} else {
			rest = append(rest, a)
		}
	}
	name, _ := nameArg(rest)
	mustExist(name)
	dir := machineDir(name)
	if _, ok := running(dir); ok {
		die("'%s' is running (kaetzli stop%s)", name, hint(name))
	}
	if !yes {
		fmt.Printf("delete '%s' and its /var disk? [y/N] ", name)
		answer, _ := bufio.NewReader(os.Stdin).ReadString('\n')
		if !strings.HasPrefix(strings.ToLower(answer), "y") {
			fmt.Println("kept")
			return
		}
	}
	if err := os.RemoveAll(dir); err != nil {
		die("%v", err)
	}
	say("deleted '%s'", name)
}

// cmdPrintArgv prints, as JSON, the command line start would run for the settings given:
// python/tests/test_launcher.py holds it to kaetzli.qemu's for the same machine.
func cmdPrintArgv(args []string) {
	var m qemu.Machine
	m.Mem, m.CPUs, m.Init, m.Reboot = "2048", "2", "/usr/lib/systemd/systemd", true
	m.Arch = arch()
	console, qmpPort, log := 0, 0, ""
	for i := 0; i+1 < len(args); i += 2 {
		k, v := args[i], args[i+1]
		switch k {
		case "--arch":
			m.Arch = v
		case "--kvm":
			m.KVM = v == "1"
		case "--mem":
			m.Mem = v
		case "--cpus":
			m.CPUs = v
		case "--image":
			m.Kernel, m.Rootfs = filepath.Join(v, "bzImage"), filepath.Join(v, "rootfs.erofs")
			m.Cmdline, m.ExtDir = filepath.Join(v, "rootfs.cmdline"), v
		case "--var":
			m.VarDisk = v
		case "--ext":
			m.Sysexts = append(m.Sysexts, v)
		case "--ssh-port":
			m.SSHPort, _ = strconv.Atoi(v)
		case "--pubkey":
			b, err := os.ReadFile(v)
			if err != nil {
				die("%v", err)
			}
			m.PubKey, m.PubKeyFile = b, v
		case "--secureboot":
			m.SecureBoot = &qemu.SecureBoot{UKI: filepath.Join(v, "kaetzli.efi"),
				Code: filepath.Join(v, "ovmf-code.fd"), Vars: filepath.Join(v, "ovmf-vars.fd")}
		case "--hostname":
			m.Hostname = v
		case "--console-port":
			console, _ = strconv.Atoi(v)
		case "--qmp-port":
			qmpPort, _ = strconv.Atoi(v)
		case "--log":
			log = v
		default:
			die("print-argv: unknown setting %s", k)
		}
	}
	m.Display = display(console, log)
	argv, err := m.Argv()
	if err != nil {
		die("%v", err)
	}
	out, _ := json.Marshal(append([]string{m.Binary()}, append(argv, qmpArgs(qmpPort)...)...))
	fmt.Println(string(out))
}

func nameArg(args []string) (string, []string) {
	if len(args) > 0 && !strings.HasPrefix(args[0], "-") {
		return args[0], args[1:]
	}
	return defaultName, args
}

func hint(name string) string {
	if name == defaultName {
		return ""
	}
	return " " + name
}

func mustExist(name string) {
	if !exists(name) {
		die("no machine '%s' (kaetzli start%s makes it)", name, hint(name))
	}
}

func describe(img string) string {
	if img == "" {
		return "newest snapshot"
	}
	if b, err := os.ReadFile(filepath.Join(img, "snapshot")); err == nil {
		return "snapshot " + strings.TrimSpace(string(b))
	}
	return img
}

func orNone(s string) string {
	if s == "" {
		return "none"
	}
	return s
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}

func isFile(p string) bool {
	st, err := os.Stat(p)
	return err == nil && st.Mode().IsRegular()
}

func tail(path string, n int) {
	b, err := os.ReadFile(path)
	if err != nil {
		return
	}
	lines := strings.Split(strings.TrimRight(string(b), "\n"), "\n")
	if len(lines) > n {
		lines = lines[len(lines)-n:]
	}
	fmt.Fprintln(os.Stderr, strings.Join(lines, "\n"))
}

const usage = `kaetzli: kaetzli virtual machines, from the latest snapshot release

  kaetzli start [NAME] [--ext a,b | --no-ext] [--mem MB] [--cpus N] [--size 8G]
                [--image DIR | --snapshot NAME|latest] [--secure-boot | --no-secure-boot]
  kaetzli ssh [--root] [NAME] [command...]
  kaetzli console [NAME]           the serial console (Ctrl-] to leave)
  kaetzli stop [NAME] [--force]
  kaetzli list
  kaetzli info [NAME]
  kaetzli update                   fetch the newest snapshot; machines boot it at their next start,
                                   and go back to their last good one if it does not come up
  kaetzli rollback [NAME]          pin NAME to the snapshot before the one it boots
  kaetzli rm [NAME] [--yes]
  kaetzli version

NAME defaults to kaetzli. Options to start are remembered. --secure-boot (amd64) boots
the image's UKI, with the extensions' verity tables in its command line, under firmware
that admits that one image and nothing else.
Machines: KAETZLI_HOME (default: the platform's data directory). Images: KAETZLI_CACHE.
`

func main() {
	if len(os.Args) < 2 {
		fmt.Print(usage)
		os.Exit(2)
	}
	args := os.Args[2:]
	switch os.Args[1] {
	case "start":
		cmdStart(args)
	case "stop":
		cmdStop(args)
	case "ssh":
		cmdSSH(args)
	case "console":
		cmdConsole(args)
	case "list":
		cmdList()
	case "info":
		cmdInfo(args)
	case "update":
		cmdUpdate()
	case "rollback":
		cmdRollback(args)
	case "rm":
		cmdRm(args)
	case "print-argv":
		cmdPrintArgv(args)
	case "version", "--version":
		fmt.Println("kaetzli " + version)
	case "help", "-h", "--help":
		fmt.Print(usage)
	default:
		fmt.Fprintf(os.Stderr, "error: unknown command '%s'\n\n%s", os.Args[1], usage)
		os.Exit(2)
	}
}
