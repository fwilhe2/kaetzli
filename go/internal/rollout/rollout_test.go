package rollout

import (
	"reflect"
	"testing"
)

// Newest first, as snapshot.Cached lists them.
var cached = []string{"20261107.000000-3", "20261106.000000-2", "20261105.000000-1"}

func TestANewMachineBootsTheNewest(t *testing.T) {
	got, err := Choose(cached, State{})
	if err != nil || got != "20261107.000000-3" {
		t.Fatalf("Choose = %q, %v", got, err)
	}
}

func TestAPinIsBootedEvenWhenNewerAreCached(t *testing.T) {
	got, err := Choose(cached, State{Pin: "20261105.000000-1"})
	if err != nil || got != "20261105.000000-1" {
		t.Fatalf("Choose = %q, %v", got, err)
	}
}

func TestAPinThatIsNotCachedIsAnError(t *testing.T) {
	if _, err := Choose(cached, State{Pin: "20261101.000000-9"}); err == nil {
		t.Fatal("a pin the cache lacks must not quietly boot something else")
	}
}

func TestASnapshotThatFailedHereIsSkipped(t *testing.T) {
	got, _ := Choose(cached, State{Good: "20261106.000000-2", Bad: []string{"20261107.000000-3"}})
	if got != "20261106.000000-2" {
		t.Fatalf("Choose = %q, want the newest not known bad", got)
	}
}

func TestOnlyBadSnapshotsCachedIsAnError(t *testing.T) {
	if _, err := Choose(cached[:1], State{Bad: cached[:1]}); err == nil {
		t.Fatal("want an error naming that every cached snapshot failed here")
	}
}

func TestRevertGoesToTheLastGoodWhenItIsCachedAndNotWhatFailed(t *testing.T) {
	s := State{Good: "20261106.000000-2"}
	if to, ok := Revert(s, "20261107.000000-3", cached); !ok || to != "20261106.000000-2" {
		t.Fatalf("Revert = %q, %v", to, ok)
	}
	if _, ok := Revert(s, "20261106.000000-2", cached); ok {
		t.Fatal("the last good one failing has nothing to revert to")
	}
	if _, ok := Revert(State{}, "20261107.000000-3", cached); ok {
		t.Fatal("a machine that never came up has no last good one")
	}
	if _, ok := Revert(State{Good: "20261101.000000-9"}, "20261107.000000-3", cached); ok {
		t.Fatal("a last good one no longer cached cannot be booted")
	}
}

func TestRevertIsNeverAutomaticForAPinnedMachine(t *testing.T) {
	s := State{Good: "20261106.000000-2", Pin: "20261107.000000-3"}
	if _, ok := Revert(s, "20261107.000000-3", cached); ok {
		t.Fatal("a pin is the user's choice; failing it is reported, not overridden")
	}
}

func TestUpAndDownRecordTheOutcome(t *testing.T) {
	s := State{Bad: []string{"x"}}
	s = s.Down("20261107.000000-3")
	s = s.Down("20261107.000000-3")
	if !reflect.DeepEqual(s.Bad, []string{"x", "20261107.000000-3"}) {
		t.Fatalf("Bad = %v, each once", s.Bad)
	}
	s = s.Up("20261107.000000-3")
	if s.Good != "20261107.000000-3" || !reflect.DeepEqual(s.Bad, []string{"x"}) {
		t.Fatalf("after Up: %+v", s)
	}
}

func TestPreviousIsTheNextOlderCachedSnapshot(t *testing.T) {
	if p := Previous(cached, "20261107.000000-3"); p != "20261106.000000-2" {
		t.Fatalf("Previous = %q", p)
	}
	if p := Previous(cached, "20261105.000000-1"); p != "" {
		t.Fatalf("the oldest has no previous, got %q", p)
	}
}

func TestKeepTheNewestThePreviousAndWhatAnyMachineNeeds(t *testing.T) {
	all := []string{"5", "4", "3", "2", "1"}
	states := []State{{Pin: "2"}, {Good: "1"}}
	got := Keep(all, states, []string{"3"})
	want := []string{"5", "4", "3", "2", "1"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("Keep = %v", got)
	}
	got = Keep(all, nil, nil)
	if !reflect.DeepEqual(got, []string{"5", "4"}) {
		t.Fatalf("Keep with no machines = %v, want the newest two", got)
	}
}
