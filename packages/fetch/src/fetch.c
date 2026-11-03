/*
 * fetch - the summary neofetch prints, for a system that will never have neofetch.
 *
 * Everything here comes from uname(2), getifaddrs(3), the environment, /proc, /sys and
 * a few files systemd leaves in /run and /usr (resolved's servers, merged extensions).
 * Nothing is executed and nothing is asked over D-Bus. There is no
 * dependency beyond glibc, which is the point twice over: nothing has to be packaged to
 * make it build, and test/check-rootfs-deps.py has nothing to report about the result.
 *
 * Two constraints from the image it ships in shape the output:
 *
 *   - ASCII only. image/build-rootfs.sh deletes share/locale and share/i18n, so the
 *     image is C-locale only and a box-drawing character would print as garbage.
 *   - Every source is optional. A field whose file is missing is left out rather than
 *     printed empty, because which files exist depends on the kernel fragment and the
 *     architecture: /sys/class/dmi wants CONFIG_DMI_SYSFS, /proc/device-tree only
 *     exists where there is a device tree, and /proc/cpuinfo names the CPU on x86 but
 *     not on arm64.
 */
#include <arpa/inet.h>
#include <dirent.h>
#include <ifaddrs.h>
#include <mntent.h>
#include <net/if.h>
#include <netinet/in.h>
#include <pwd.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/sysmacros.h>
#include <sys/utsname.h>
#include <unistd.h>

#define VAL_MAX  192
#define KEY_MAX  16
#define MAX_INFO 28

/* Set from main() once, so every printf below can stay unconditional. */
static const char *c_logo = "";
static const char *c_key = "";
static const char *c_off = "";

/* A Kaetzli is a kitten. Backslashes doubled for C; the picture is what is between the
 * quotes with every \\ read as one \. */
static const char *logo[] = {
	"      /\\_____/\\",
	"     /  o   o  \\",
	"    ( ==  ^  == )",
	"     )   '-'   (",
	"    (           )",
	"   ( (  )   (  ) )",
	"  (__(__)___(__)__)",
	"         \\",
	"          )",
	"    ~ kaetzli ~",
};
#define LOGO_LINES ((int)(sizeof logo / sizeof logo[0]))

static struct {
	char key[KEY_MAX];
	char val[VAL_MAX];
} info[MAX_INFO];
static int info_n;

/* A key of "" prints the value alone, which is what the header and the separator want. */
static void add(const char *key, const char *fmt, ...)
{
	va_list ap;

	if (info_n >= MAX_INFO)
		return;
	snprintf(info[info_n].key, KEY_MAX, "%s", key);
	va_start(ap, fmt);
	vsnprintf(info[info_n].val, VAL_MAX, fmt, ap);
	va_end(ap);
	info_n++;
}

static void trim(char *s)
{
	size_t n = strlen(s);

	while (n > 0 && (unsigned char)s[n - 1] <= ' ')
		s[--n] = '\0';
	if (s[0] == '\0')
		return;
	size_t lead = strspn(s, " \t");
	if (lead)
		memmove(s, s + lead, strlen(s + lead) + 1);
}

/* First line of a file, trimmed. Zero on success, so callers can chain fallbacks. */
static int read_line(const char *path, char *out, size_t n)
{
	FILE *f = fopen(path, "re");

	if (!f)
		return -1;
	if (!fgets(out, (int)n, f)) {
		fclose(f);
		return -1;
	}
	fclose(f);
	trim(out);
	return out[0] ? 0 : -1;
}

/*
 * Value of `key <sep> value` in a colon-separated table: /proc/meminfo, /proc/cpuinfo.
 * Case-insensitive because the same information is spelled "model name" on x86 and
 * "Model" or "Hardware" elsewhere, and there is no reason to care which.
 */
static int read_keyed(const char *path, const char *key, char *out, size_t n)
{
	char line[512];
	FILE *f = fopen(path, "re");

	if (!f)
		return -1;
	while (fgets(line, sizeof line, f)) {
		char *sep = strchr(line, ':');

		if (!sep)
			continue;
		*sep = '\0';
		trim(line);
		if (strcasecmp(line, key) != 0)
			continue;
		snprintf(out, n, "%s", sep + 1);
		trim(out);
		fclose(f);
		return out[0] ? 0 : -1;
	}
	fclose(f);
	return -1;
}

/* KEY=value or KEY="value" out of an os-release file. */
static int read_osrelease(const char *key, char *out, size_t n)
{
	char line[512];
	FILE *f = fopen("/etc/os-release", "re");
	size_t keylen = strlen(key);

	if (!f)
		return -1;
	while (fgets(line, sizeof line, f)) {
		if (strncmp(line, key, keylen) != 0 || line[keylen] != '=')
			continue;
		char *val = line + keylen + 1;
		trim(val);
		size_t len = strlen(val);
		if (len >= 2 && val[0] == '"' && val[len - 1] == '"') {
			val[len - 1] = '\0';
			val++;
		}
		snprintf(out, n, "%s", val);
		fclose(f);
		return out[0] ? 0 : -1;
	}
	fclose(f);
	return -1;
}

static unsigned long long meminfo_kb(const char *key)
{
	char val[VAL_MAX];

	if (read_keyed("/proc/meminfo", key, val, sizeof val) != 0)
		return 0;
	return strtoull(val, NULL, 10);
}

/* MiB below a gibibyte, GiB above it, so neither a 512 MiB VM nor a 40 GiB disk reads
 * as noise. Kibibytes in, because that is the unit both /proc/meminfo and the block
 * arithmetic below already work in. */
static void human_kb(unsigned long long kb, char *out, size_t n)
{
	if (kb >= 1024ULL * 1024ULL)
		snprintf(out, n, "%.1f GiB", (double)kb / (1024.0 * 1024.0));
	else
		snprintf(out, n, "%llu MiB", kb / 1024ULL);
}

static void add_uptime(void)
{
	char buf[64], out[VAL_MAX];
	double secs;

	if (read_line("/proc/uptime", buf, sizeof buf) != 0)
		return;
	secs = strtod(buf, NULL);

	unsigned long total = (unsigned long)secs;
	unsigned long days = total / 86400;
	unsigned long hours = (total % 86400) / 3600;
	unsigned long mins = (total % 3600) / 60;
	int len = 0;

	out[0] = '\0';
	if (days)
		len += snprintf(out + len, sizeof out - (size_t)len, "%lu day%s", days,
				days == 1 ? "" : "s");
	if (hours)
		len += snprintf(out + len, sizeof out - (size_t)len, "%s%lu hour%s",
				len ? ", " : "", hours, hours == 1 ? "" : "s");
	/* Only bother with minutes when they are the largest unit or close to it: "2 days,
	 * 3 hours, 7 mins" is more precision than an uptime line has ever needed. */
	if (!days && (mins || !hours))
		len += snprintf(out + len, sizeof out - (size_t)len, "%s%lu min%s",
				len ? ", " : "", mins, mins == 1 ? "" : "s");
	add("Uptime", "%s", out);
}

static void add_cpu(void)
{
	static const char *keys[] = { "model name", "Hardware", "Model", "cpu model" };
	char model[VAL_MAX] = "";
	long ncpu = sysconf(_SC_NPROCESSORS_ONLN);

	for (size_t i = 0; i < sizeof keys / sizeof keys[0]; i++)
		if (read_keyed("/proc/cpuinfo", keys[i], model, sizeof model) == 0)
			break;

	/* arm64's /proc/cpuinfo has no model string at all — only CPU implementer/part
	 * numbers, which would need a lookup table to be worth printing. The machine name
	 * is honest and costs nothing. */
	if (!model[0]) {
		struct utsname u;

		if (uname(&u) == 0)
			snprintf(model, sizeof model, "%s", u.machine);
	}
	if (!model[0])
		return;

	if (ncpu > 0)
		add("CPU", "%s (%ld)", model, ncpu);
	else
		add("CPU", "%s", model);
}

static void add_memory(void)
{
	unsigned long long total = meminfo_kb("MemTotal");
	unsigned long long avail = meminfo_kb("MemAvailable");
	char used_h[32], total_h[32];

	if (!total)
		return;
	/* MemAvailable, not MemFree: page cache is not memory anyone is missing. Older
	 * kernels without it fall back to counting everything as used, which is at least
	 * not a lie about the total. */
	unsigned long long used = avail <= total ? total - avail : total;

	human_kb(used, used_h, sizeof used_h);
	human_kb(total, total_h, sizeof total_h);
	add("Memory", "%s / %s (%llu%%)", used_h, total_h, used * 100 / total);
}

static void add_disk(void)
{
	struct statvfs s;
	char used_h[32], total_h[32];

	if (statvfs("/", &s) != 0 || s.f_blocks == 0)
		return;

	/* f_bavail rather than f_bfree: the root-reserved blocks are not space this
	 * system can spend, so counting them as free would overstate what is left. */
	unsigned long long unit = (unsigned long long)(s.f_frsize ? s.f_frsize : s.f_bsize);
	unsigned long long total = (unsigned long long)s.f_blocks * unit / 1024;
	unsigned long long used = (unsigned long long)(s.f_blocks - s.f_bavail) * unit / 1024;

	if (!total)
		return;
	human_kb(used, used_h, sizeof used_h);
	human_kb(total, total_h, sizeof total_h);
	add("Disk (/)", "%s / %s (%llu%%)", used_h, total_h, used * 100 / total);
}

static void add_load(void)
{
	char buf[128];
	double a, b, c;

	if (read_line("/proc/loadavg", buf, sizeof buf) != 0)
		return;
	if (sscanf(buf, "%lf %lf %lf", &a, &b, &c) != 3)
		return;
	add("Load", "%.2f %.2f %.2f", a, b, c);
}

static void add_host(void)
{
	char buf[VAL_MAX];

	/* x86 under qemu answers the first ("Standard PC (i440FX + PIIX, 1996)"); the
	 * arm64 virt board has no DMI but does have a device tree, whose model is
	 * "linux,dummy-virt". Neither is guaranteed, hence two tries and no third. */
	if (read_line("/sys/devices/virtual/dmi/id/product_name", buf, sizeof buf) == 0 ||
	    read_line("/proc/device-tree/model", buf, sizeof buf) == 0)
		add("Host", "%s", buf);
}

static void add_os(void)
{
	char buf[VAL_MAX];

	if (read_osrelease("PRETTY_NAME", buf, sizeof buf) == 0 ||
	    read_osrelease("NAME", buf, sizeof buf) == 0 ||
	    read_osrelease("ID", buf, sizeof buf) == 0)
		add("OS", "%s", buf);
}

static void add_header(void)
{
	struct passwd *pw = getpwuid(getuid());
	const char *user = pw && pw->pw_name ? pw->pw_name : getenv("USER");
	char host[VAL_MAX];
	char sep[VAL_MAX];

	if (gethostname(host, sizeof host) != 0)
		snprintf(host, sizeof host, "unknown");
	host[sizeof host - 1] = '\0';
	if (!user)
		user = "unknown";

	add("", "%s@%s", user, host);

	size_t n = strlen(user) + 1 + strlen(host);
	if (n >= sizeof sep)
		n = sizeof sep - 1;
	memset(sep, '-', n);
	sep[n] = '\0';
	add("", "%s", sep);
}

/*
 * The last /proc/self/mounts entry for a mount point, which is the one on top: a sysext
 * merge mounts an overlay over /usr without unmounting what was there. Zero if found.
 */
static int find_mount(const char *dir, char *fsname, size_t fsn, char *type, size_t tn,
		      int *ro)
{
	FILE *f = setmntent("/proc/self/mounts", "re");
	struct mntent *m;
	int found = -1;

	if (!f)
		return -1;
	while ((m = getmntent(f))) {
		if (strcmp(m->mnt_dir, dir) != 0)
			continue;
		snprintf(fsname, fsn, "%s", m->mnt_fsname);
		snprintf(type, tn, "%s", m->mnt_type);
		*ro = hasmntopt(m, "ro") != NULL;
		found = 0;
	}
	endmntent(f);
	return found;
}

/* /proc/cmdline, whole. Zero on success. */
static int read_cmdline(char *buf, size_t n)
{
	FILE *f = fopen("/proc/cmdline", "re");
	size_t got;

	if (!f)
		return -1;
	got = fread(buf, 1, n - 1, f);
	fclose(f);
	buf[got] = '\0';
	return got ? 0 : -1;
}

/*
 * The root's verity table, out of dm-mod.create= (rootfs.cmdline). The table itself is
 * only readable through /dev/mapper/control, which is root's, and this runs as anyone;
 * the command line is world-readable and is where the table came from. The root is the
 * device named "root" — extensions' tables are joined to it with ';' (qemu_sysexts) —
 * and its root hash is the ninth field after "verity":
 *
 *   verity <version> <data dev> <hash dev> <data bs> <hash bs> <blocks> <hash start>
 *          <algorithm> <root hash> <salt>
 *
 * That hash is the image's identity, to the bit: os-release has no build number, and
 * two images with the same hash are the same image.
 */
static int root_hash(char *out, size_t n)
{
	char buf[4096];

	if (read_cmdline(buf, sizeof buf) != 0)
		return -1;
	char *dm = strstr(buf, "dm-mod.create=");
	if (!dm)
		return -1;
	dm += strlen("dm-mod.create=");
	if (*dm == '"')
		dm++;
	char *end = strpbrk(dm, ";\"");
	if (end)
		*end = '\0';
	if (strncmp(dm, "root,", 5) != 0)
		return -1;
	char *v = strstr(dm, " verity ");
	if (!v)
		return -1;

	char *save, *tok = strtok_r(v, " ", &save);
	for (int i = 0; tok && i < 9; i++)
		tok = strtok_r(NULL, " ", &save);
	if (!tok || strspn(tok, "0123456789abcdef") != strlen(tok))
		return -1;
	snprintf(out, n, "%s", tok);
	return 0;
}

/*
 * The OCI flavour runs on whatever the engine gives it, usually an overlay, and none of
 * the disk image's arrangement applies. podman writes the first, docker the second, and
 * systemd-nspawn and others set $container for PID 1, which only PID 1 sees.
 */
static int in_container(void)
{
	return access("/run/.containerenv", F_OK) == 0 || access("/.dockerenv", F_OK) == 0;
}

/*
 * Whether / is on a device-mapper device. Not by name: the mount table says /dev/root
 * for whatever the kernel mounted from root=, so the device number is the only honest
 * answer, and sysfs has a dm/ directory under exactly the block devices that are dm's.
 */
static int root_is_dm(void)
{
	struct stat st;
	char path[64];

	if (stat("/", &st) != 0)
		return 0;
	snprintf(path, sizeof path, "/sys/dev/block/%u:%u/dm", major(st.st_dev),
		 minor(st.st_dev));
	return access(path, F_OK) == 0;
}

static void add_root(void)
{
	char fsname[VAL_MAX], type[32];
	int ro;

	if (find_mount("/", fsname, sizeof fsname, type, sizeof type, &ro) != 0)
		return;
	if (in_container()) {
		add("Root", "%s, %s (container)", type, ro ? "read-only" : "writable");
		return;
	}
	/* Verity is claimed only when the root actually is a device-mapper device: a
	 * dm-mod.create= on the command line says what was built, not what was mounted. */
	char hash[VAL_MAX];

	if (root_is_dm() && root_hash(hash, sizeof hash) == 0)
		add("Root", "%s, %s, dm-verity", type, ro ? "read-only" : "writable");
	else
		add("Root", "%s on %s, %s", type, fsname, ro ? "read-only" : "writable");
}

/* Twelve hex digits, as git abbreviates: enough to tell builds apart at a glance. The
 * full hash is in /proc/cmdline for anyone who needs to compare it with rootfs.cmdline. */
static void add_image(void)
{
	char hash[VAL_MAX];

	if (in_container() || !root_is_dm())
		return;
	if (root_hash(hash, sizeof hash) == 0)
		add("Image", "%.12s (verity root hash)", hash);
}

/*
 * Every IPv4 address on an interface that is up, other than loopback: the one thing
 * needed before ssh, and the quickest answer to "did networkd do its job". IPv6 link-local
 * addresses are skipped as noise; a global IPv6 address is shown like an IPv4 one.
 */
static void add_network(void)
{
	struct ifaddrs *ifa, *i;
	char out[VAL_MAX] = "";
	size_t len = 0;

	if (getifaddrs(&ifa) != 0)
		return;
	for (int family = 0; family < 2; family++) {
		for (i = ifa; i; i = i->ifa_next) {
			char addr[INET6_ADDRSTRLEN];
			int af = family ? AF_INET6 : AF_INET;
			int prefix = 0;

			if (!i->ifa_addr || i->ifa_addr->sa_family != af)
				continue;
			if (!(i->ifa_flags & IFF_UP) || (i->ifa_flags & IFF_LOOPBACK))
				continue;
			if (af == AF_INET) {
				struct sockaddr_in *a = (struct sockaddr_in *)i->ifa_addr;
				struct sockaddr_in *m = (struct sockaddr_in *)i->ifa_netmask;

				inet_ntop(AF_INET, &a->sin_addr, addr, sizeof addr);
				if (m)
					prefix = __builtin_popcount(m->sin_addr.s_addr);
			} else {
				struct sockaddr_in6 *a = (struct sockaddr_in6 *)i->ifa_addr;
				struct sockaddr_in6 *m = (struct sockaddr_in6 *)i->ifa_netmask;

				if (IN6_IS_ADDR_LINKLOCAL(&a->sin6_addr))
					continue;
				inet_ntop(AF_INET6, &a->sin6_addr, addr, sizeof addr);
				for (int b = 0; m && b < 16; b++)
					prefix += __builtin_popcount(m->sin6_addr.s6_addr[b]);
			}
			int w = snprintf(out + len, sizeof out - len, "%s%s %s/%d",
					 len ? ", " : "", i->ifa_name, addr, prefix);
			if (w < 0 || (size_t)w >= sizeof out - len)
				goto done;
			len += (size_t)w;
		}
	}
done:
	freeifaddrs(ifa);
	if (len)
		add("Network", "%s", out);
}

/*
 * The servers resolved actually uses, which is what DHCP handed networkd — not
 * /etc/resolv.conf, which points at resolved's stub (127.0.0.53) and so says nothing.
 */
static void add_dns(void)
{
	char line[256], out[VAL_MAX] = "";
	size_t len = 0;
	FILE *f = fopen("/run/systemd/resolve/resolv.conf", "re");

	if (!f)
		return;
	while (fgets(line, sizeof line, f)) {
		char addr[INET6_ADDRSTRLEN + 1];

		if (sscanf(line, "nameserver %46s", addr) != 1)
			continue;
		int w = snprintf(out + len, sizeof out - len, "%s%s", len ? ", " : "", addr);
		if (w < 0 || (size_t)w >= sizeof out - len)
			break;
		len += (size_t)w;
	}
	fclose(f);
	if (len)
		add("DNS", "%s", out);
}

/*
 * Where state lives is the one thing about this image a user most needs to know before
 * they install something: /var is tmpfs unless the VM handed it a disk with
 * systemd.mount-extra=, and /home and /root are symlinks into it, so everything written
 * outside /tmp and /run goes where /var goes.
 */
static void add_state(void)
{
	char fsname[VAL_MAX], type[32];
	int ro;

	if (find_mount("/var", fsname, sizeof fsname, type, sizeof type, &ro) != 0) {
		/* No mount of its own: /var is the root's, as in a container. */
		if (find_mount("/", fsname, sizeof fsname, type, sizeof type, &ro) == 0)
			add("State", "/var on the root (%s)", ro ? "read-only" : "writable");
		return;
	}
	if (!strcmp(type, "tmpfs"))
		add("State", "in memory (/var is tmpfs, lost at shutdown)");
	else
		add("State", "persistent (/var is %s on %s%s)", type, fsname,
		    ro ? ", read-only" : "");
}

/*
 * A merged system extension leaves its extension-release file under /usr, so the
 * directory lists exactly what is merged now — not what is on disk waiting to be.
 */
static int is_extension_release(const struct dirent *e)
{
	return strncmp(e->d_name, "extension-release.", 18) == 0;
}

static void add_extensions(void)
{
	struct dirent **list;
	char out[VAL_MAX] = "";
	size_t len = 0;
	/* alphasort, because readdir's order is the filesystem's and the same set of
	 * extensions should print the same way every boot. */
	int n = scandir("/usr/lib/extension-release.d", &list, is_extension_release, alphasort);

	if (n < 0)
		return;
	for (int i = 0; i < n; i++) {
		int w = len >= sizeof out ? -1 :
			snprintf(out + len, sizeof out - len, "%s%s", len ? ", " : "",
				 list[i]->d_name + 18);

		if (w >= 0 && (size_t)w < sizeof out - len)
			len += (size_t)w;
		else
			len = sizeof out;
		free(list[i]);
	}
	free(list);
	if (len && out[0])
		add("Extensions", "%s", out);
}

/* neofetch's colour bar, in a form that survives a C-locale console: background colours
 * behind spaces rather than block-drawing characters. */
static void print_swatch(int pad)
{
	if (!*c_logo)
		return;
	/* The dim row is 40-47 and the bright row 100-107 — the two standard background
	 * ranges, so this needs no 256-colour or truecolour support from the terminal. */
	static const int base[] = { 40, 100 };

	for (size_t row = 0; row < sizeof base / sizeof base[0]; row++) {
		printf("%*s  ", pad, "");
		for (int i = 0; i < 8; i++)
			printf("\033[%dm   ", base[row] + i);
		printf("\033[0m\n");
	}
}

static void usage(void)
{
	puts("usage: fetch [--no-color] [--help]");
	puts("");
	puts("Print a short summary of this system. Colour is used when stdout is a");
	puts("terminal and NO_COLOR is unset.");
}

int main(int argc, char **argv)
{
	int color = isatty(STDOUT_FILENO) && !getenv("NO_COLOR");
	struct utsname u;
	char buf[VAL_MAX];

	for (int i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "--no-color") || !strcmp(argv[i], "--no-colour")) {
			color = 0;
		} else if (!strcmp(argv[i], "-h") || !strcmp(argv[i], "--help")) {
			usage();
			return 0;
		} else {
			fprintf(stderr, "fetch: unknown option: %s\n", argv[i]);
			return 2;
		}
	}

	if (color) {
		c_logo = "\033[1;36m";
		c_key = "\033[1;36m";
		c_off = "\033[0m";
	}

	add_header();
	add_os();
	add_host();
	if (uname(&u) == 0) {
		add("Kernel", "%s", u.release);
		add("Arch", "%s", u.machine);
	}
	if (read_line("/proc/1/comm", buf, sizeof buf) == 0)
		add("Init", "%s", buf);
	add_image();
	add_root();
	add_state();
	add_extensions();
	add_network();
	add_dns();
	add_uptime();
	add_load();
	add_cpu();
	add_memory();
	add_disk();
	if (getenv("SHELL"))
		add("Shell", "%s", getenv("SHELL"));
	if (getenv("TERM"))
		add("Terminal", "%s", getenv("TERM"));

	int width = 0;
	for (int i = 0; i < LOGO_LINES; i++) {
		int len = (int)strlen(logo[i]);

		if (len > width)
			width = len;
	}

	int rows = LOGO_LINES > info_n ? LOGO_LINES : info_n;

	putchar('\n');
	for (int i = 0; i < rows; i++) {
		printf("%s%-*s%s  ", c_logo, width, i < LOGO_LINES ? logo[i] : "", c_off);
		if (i < info_n) {
			if (info[i].key[0])
				printf("%s%s%s: ", c_key, info[i].key, c_off);
			fputs(info[i].val, stdout);
		}
		putchar('\n');
	}
	putchar('\n');
	print_swatch(width);

	return 0;
}
