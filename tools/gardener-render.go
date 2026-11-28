// Render Gardener's node init script as Gardener renders it: text/template, the data
// nodeinit.generateInitScript passes when no registry CA is configured. Go, not Python,
// because the point is text/template's own trimming and actions, not a reading of them.
// Standard library only, run with `go run` by kaetzli.updates.gardener.
//
//	go run tools/gardener-render.go <init.tpl.sh> <image>
package main

import (
	"fmt"
	"os"
	"text/template"
)

func main() {
	if len(os.Args) != 3 {
		fmt.Fprintln(os.Stderr, "usage: go run tools/gardener-render.go <init.tpl.sh> <image>")
		os.Exit(2)
	}
	content, err := os.ReadFile(os.Args[1])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	tpl := template.Must(template.New("init-script").Parse(string(content)))
	data := map[string]any{
		"image":           os.Args[2],
		"binaryName":      "gardener-node-agent",
		"binaryDirectory": "/opt/bin",
		"configDir":       "/var/lib/gardener-node-agent",
	}
	if err := tpl.Execute(os.Stdout, data); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
