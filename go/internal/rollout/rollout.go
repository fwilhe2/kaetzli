// Package rollout decides which snapshot a machine boots, when a failed boot reverts to the
// last one that came up, and which cached snapshots `kaetzli update` keeps. Pure functions
// over snapshot names (newest first, as snapshot.Cached lists them) and a machine's State;
// the launcher does the booting.
package rollout

import (
	"fmt"
	"slices"
)

// State is what a machine remembers about snapshots, in its config.json.
type State struct {
	Pin  string   `json:"pin,omitempty"`  // boot exactly this one; "" follows the newest
	Good string   `json:"good,omitempty"` // the last one that came up (ssh answered)
	Bad  []string `json:"bad,omitempty"`  // ones that did not come up here: skipped from now on
}

// Choose is the snapshot to boot: the pin, or else the newest cached one this machine has
// not seen fail.
func Choose(cached []string, s State) (string, error) {
	if s.Pin != "" {
		if !slices.Contains(cached, s.Pin) {
			return "", fmt.Errorf("pinned to snapshot %s, which is not cached (kaetzli update, or start --snapshot latest)", s.Pin)
		}
		return s.Pin, nil
	}
	for _, c := range cached {
		if !slices.Contains(s.Bad, c) {
			return c, nil
		}
	}
	return "", fmt.Errorf("every cached snapshot failed to come up on this machine (%v); kaetzli update fetches a newer one", s.Bad)
}

// Revert is where a machine goes after `booted` failed to come up: its last good snapshot,
// if that is another one and still cached. Never for a pinned machine — a pin is a choice,
// and its failure is reported rather than overridden.
func Revert(s State, booted string, cached []string) (string, bool) {
	if s.Pin != "" || s.Good == "" || s.Good == booted || !slices.Contains(cached, s.Good) {
		return "", false
	}
	return s.Good, true
}

// Up records that name came up: it is the last good one, and no longer bad.
func (s State) Up(name string) State {
	s.Good = name
	s.Bad = slices.DeleteFunc(slices.Clone(s.Bad), func(b string) bool { return b == name })
	return s
}

// Down records that name did not come up.
func (s State) Down(name string) State {
	if !slices.Contains(s.Bad, name) {
		s.Bad = append(slices.Clone(s.Bad), name)
	}
	return s
}

// Previous is the cached snapshot just older than current: what `kaetzli rollback` pins.
func Previous(cached []string, current string) string {
	i := slices.Index(cached, current)
	if i < 0 || i+1 >= len(cached) {
		return ""
	}
	return cached[i+1]
}

// Keep is what `kaetzli update` leaves in the cache: the newest two, so there is always one
// to go back to, and every snapshot a machine is pinned to, last came up on, or is
// running. Newest first.
func Keep(cached []string, states []State, inUse []string) []string {
	var out []string
	for i, c := range cached {
		need := i < 2 || slices.Contains(inUse, c)
		for _, s := range states {
			need = need || s.Pin == c || s.Good == c
		}
		if need {
			out = append(out, c)
		}
	}
	return out
}
