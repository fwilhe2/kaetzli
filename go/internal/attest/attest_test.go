package attest

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

// testdata/attestations.json is what GitHub's API returned for a snapshot's SHA256SUMS
// (testdata/digest.txt): the release job's SLSA provenance, logged in Rekor, and GitHub's
// own release attestation, which has no log entry.
func fixture(t *testing.T) ([]json.RawMessage, [32]byte) {
	t.Helper()
	data, err := os.ReadFile("testdata/attestations.json")
	if err != nil {
		t.Fatal(err)
	}
	var out struct {
		Attestations []struct {
			Bundle json.RawMessage `json:"bundle"`
		} `json:"attestations"`
	}
	if err := json.Unmarshal(data, &out); err != nil {
		t.Fatal(err)
	}
	var bundles []json.RawMessage
	for _, a := range out.Attestations {
		bundles = append(bundles, a.Bundle)
	}
	d, _ := os.ReadFile("testdata/digest.txt")
	raw, err := hex.DecodeString(strings.TrimSpace(string(d)))
	if err != nil || len(raw) != 32 {
		t.Fatalf("digest.txt: %v", err)
	}
	var digest [32]byte
	copy(digest[:], raw)
	return bundles, digest
}

func root(t *testing.T) *Root {
	t.Helper()
	r, err := PublicGood()
	if err != nil {
		t.Fatal(err)
	}
	return r
}

var policy = ForRepo("fwilhe2/kaetzli")

func TestTheReleaseProvenanceVerifies(t *testing.T) {
	bundles, digest := fixture(t)
	if err := root(t).VerifyAny(bundles, digest, policy); err != nil {
		t.Fatal(err)
	}
	if err := root(t).Verify(bundles[0], digest, policy); err != nil {
		t.Fatal(err)
	}
}

func TestGitHubsOwnReleaseAttestationIsNotOne(t *testing.T) {
	bundles, digest := fixture(t)
	err := root(t).Verify(bundles[1], digest, policy)
	if err == nil || !strings.Contains(err.Error(), "transparency log entries") {
		t.Fatalf("got %v", err)
	}
}

func TestAnotherDigestIsRefused(t *testing.T) {
	bundles, _ := fixture(t)
	err := root(t).VerifyAny(bundles, sha256.Sum256([]byte("other")), policy)
	if err == nil || !strings.Contains(err.Error(), "no subject has sha256") {
		t.Fatalf("got %v", err)
	}
}

func TestAnotherWorkflowIsRefused(t *testing.T) {
	bundles, digest := fixture(t)
	for _, p := range []Policy{ForRepo("someone/kaetzli"), {Workflow: strings.Replace(policy.Workflow, "main", "dev", 1)}} {
		err := root(t).Verify(bundles[0], digest, p)
		if err == nil || !strings.Contains(err.Error(), "signed by") {
			t.Fatalf("%s: got %v", p.Workflow, err)
		}
	}
}

// edit decodes the first bundle, lets f change it, and encodes it again.
func edit(t *testing.T, b json.RawMessage, f func(m map[string]any)) json.RawMessage {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	f(m)
	out, _ := json.Marshal(m)
	return out
}

func tlog(m map[string]any) map[string]any {
	return m["verificationMaterial"].(map[string]any)["tlogEntries"].([]any)[0].(map[string]any)
}

func flip(s string) string {
	raw, _ := base64.StdEncoding.DecodeString(s)
	raw[len(raw)/2] ^= 1
	return base64.StdEncoding.EncodeToString(raw)
}

func TestTamperingIsRefusedWhereverItIs(t *testing.T) {
	bundles, digest := fixture(t)
	cases := map[string]struct {
		f    func(m map[string]any)
		want string
	}{
		"payload": {func(m map[string]any) {
			env := m["dsseEnvelope"].(map[string]any)
			env["payload"] = flip(env["payload"].(string))
		}, "envelope's signature"},
		"certificate": {func(m map[string]any) {
			c := m["verificationMaterial"].(map[string]any)["certificate"].(map[string]any)
			c["rawBytes"] = flip(c["rawBytes"].(string))
		}, "certificate"},
		"signed entry timestamp": {func(m map[string]any) {
			p := tlog(m)["inclusionPromise"].(map[string]any)
			p["signedEntryTimestamp"] = flip(p["signedEntryTimestamp"].(string))
		}, "signed entry timestamp"},
		"integrated time": {func(m map[string]any) {
			tlog(m)["integratedTime"] = "1795253373"
		}, "signed entry timestamp"},
		"a proof hash": {func(m map[string]any) {
			h := tlog(m)["inclusionProof"].(map[string]any)["hashes"].([]any)
			h[3] = flip(h[3].(string))
		}, "inclusion proof"},
		"the checkpoint's signature": {func(m map[string]any) {
			c := tlog(m)["inclusionProof"].(map[string]any)["checkpoint"].(map[string]any)
			env := c["envelope"].(string)
			i := strings.LastIndex(env, " ")
			c["envelope"] = env[:i+1] + flip(strings.TrimSpace(env[i+1:])) + "\n"
		}, "checkpoint: no signature"},
		"another log": {func(m map[string]any) {
			tlog(m)["logId"] = map[string]any{"keyId": base64.StdEncoding.EncodeToString(make([]byte, 32))}
		}, "does not vouch"},
	}
	for name, c := range cases {
		err := root(t).Verify(edit(t, bundles[0], c.f), digest, policy)
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Errorf("%s: got %v, want an error about %q", name, err, c.want)
		}
	}
}

func TestInclusionProofEdges(t *testing.T) {
	leaf := make([]byte, 32)
	if r, err := rootFromInclusion(0, 1, leaf, nil); err != nil || string(r) != string(leaf) {
		t.Fatalf("a one-leaf tree is its leaf: %v", err)
	}
	if _, err := rootFromInclusion(1, 1, leaf, nil); err == nil {
		t.Fatal("an index outside the tree passed")
	}
	if _, err := rootFromInclusion(0, 2, leaf, nil); err == nil {
		t.Fatal("a proof too short passed")
	}
	if _, err := rootFromInclusion(0, 2, leaf, [][]byte{leaf, leaf}); err == nil {
		t.Fatal("a proof too long passed")
	}
}

func TestFetchAsksTheAPIForTheDigest(t *testing.T) {
	data, _ := os.ReadFile("testdata/attestations.json")
	_, digest := fixture(t)
	var asked string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		asked = r.URL.Path
		if strings.HasSuffix(r.URL.Path, hex.EncodeToString(digest[:])) {
			w.Write(data)
			return
		}
		http.NotFound(w, r)
	}))
	defer srv.Close()
	bundles, err := Fetch(srv.Client(), srv.URL, "fwilhe2/kaetzli", digest)
	if err != nil || len(bundles) != 2 {
		t.Fatalf("%d bundles, %v", len(bundles), err)
	}
	if want := "/repos/fwilhe2/kaetzli/attestations/sha256:" + hex.EncodeToString(digest[:]); asked != want {
		t.Fatalf("asked %s", asked)
	}
	none, err := Fetch(srv.Client(), srv.URL, "fwilhe2/kaetzli", sha256.Sum256(nil))
	if err != nil || none != nil {
		t.Fatalf("a digest with none: %v %v", none, err)
	}
	if err := root(t).VerifyAny(none, digest, policy); err == nil || !strings.Contains(err.Error(), "no attestation for") {
		t.Fatalf("got %v", err)
	}
}
