// Package attest verifies, with the standard library alone, that a snapshot's SHA256SUMS
// carries a build provenance attestation made by this repository's CI on main — what
// `gh attestation verify SHA256SUMS -R <repo>` checks in tools/fetch-image.sh, for hosts
// without gh (docs/proposals/always-latest.md, gap 7).
//
// The release job attests every asset and SHA256SUMS itself (actions/attest, public-good
// Sigstore, since the repository is public). A bundle passes when all of this holds:
//
//   - its certificate chains to Fulcio's root in trusted_root.json at the time Rekor
//     logged it, is for code signing, and names the workflow (SAN URI
//     https://github.com/<repo>/.github/workflows/ci.yml@refs/heads/main) and GitHub's
//     OIDC issuer (extension 1.3.6.1.4.1.57264.1.8);
//   - the DSSE envelope's signature verifies with that certificate's key, and its
//     payload is an in-toto statement of SLSA provenance v1 with the digest as a subject;
//   - the Rekor entry is for exactly that envelope (payload hash, signature, certificate),
//     its signed entry timestamp verifies with Rekor's key, and its inclusion proof leads
//     to the root of a checkpoint Rekor signed.
//
// trusted_root.json is a pinned copy of Sigstore's public-good trusted root, the first
// line of `gh attestation trusted-root` (which gh fetches through Sigstore's TUF
// repository). Sigstore rotates it rarely; a rotation is a reviewed commit replacing it.
package attest

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/sha256"
	"crypto/x509"
	_ "embed"
	"encoding/asn1"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
)

//go:embed trusted_root.json
var trustedRootJSON []byte

const (
	oidcIssuer    = "https://token.actions.githubusercontent.com"
	predicateSLSA = "https://slsa.dev/provenance/v1"
	statementV1   = "https://in-toto.io/Statement/v1"
	dssePayload   = "application/vnd.in-toto+json"
)

var oidIssuerV2 = asn1.ObjectIdentifier{1, 3, 6, 1, 4, 1, 57264, 1, 8}

// Policy is who must have made the attestation.
type Policy struct {
	Workflow string // the certificate's SAN URI, exactly
}

// ForRepo is CI's identity in repository owner/name: ci.yml, on main.
func ForRepo(repo string) Policy {
	return Policy{Workflow: "https://github.com/" + repo + "/.github/workflows/ci.yml@refs/heads/main"}
}

// --- the trusted root ------------------------------------------------------------------

type validity struct {
	Start time.Time  `json:"start"`
	End   *time.Time `json:"end"`
}

func (v validity) covers(t time.Time) bool {
	return !t.Before(v.Start) && (v.End == nil || !t.After(*v.End))
}

type trustedRoot struct {
	Tlogs []struct {
		LogID struct {
			KeyID []byte `json:"keyId"`
		} `json:"logId"`
		PublicKey struct {
			RawBytes   []byte   `json:"rawBytes"`
			KeyDetails string   `json:"keyDetails"`
			ValidFor   validity `json:"validFor"`
		} `json:"publicKey"`
	} `json:"tlogs"`
	CertificateAuthorities []struct {
		CertChain struct {
			Certificates []struct {
				RawBytes []byte `json:"rawBytes"`
			} `json:"certificates"`
		} `json:"certChain"`
		ValidFor validity `json:"validFor"`
	} `json:"certificateAuthorities"`
}

// Root is a parsed trusted root.
type Root struct {
	logs map[string]logKey // by hex log ID
	cas  []ca
}

type logKey struct {
	key      *ecdsa.PublicKey
	validFor validity
}

type ca struct {
	roots, intermediates *x509.CertPool
	validFor             validity
}

// PublicGood is Sigstore's public-good trusted root, as pinned in this package.
func PublicGood() (*Root, error) { return ParseRoot(trustedRootJSON) }

// ParseRoot reads a trusted root in Sigstore's JSON form. Logs whose key is not ECDSA
// P-256 (Rekor v2's Ed25519 shards) are left out: no bundle of ours names one, and one
// that did would be refused for naming an unknown log.
func ParseRoot(data []byte) (*Root, error) {
	var tr trustedRoot
	if err := json.Unmarshal(data, &tr); err != nil {
		return nil, fmt.Errorf("trusted root: %w", err)
	}
	r := &Root{logs: map[string]logKey{}}
	for _, l := range tr.Tlogs {
		if l.PublicKey.KeyDetails != "PKIX_ECDSA_P256_SHA_256" {
			continue
		}
		k, err := x509.ParsePKIXPublicKey(l.PublicKey.RawBytes)
		if err != nil {
			return nil, fmt.Errorf("trusted root: a log key: %w", err)
		}
		ek, ok := k.(*ecdsa.PublicKey)
		if !ok {
			return nil, errors.New("trusted root: a P-256 log key that is not ECDSA")
		}
		r.logs[hex.EncodeToString(l.LogID.KeyID)] = logKey{ek, l.PublicKey.ValidFor}
	}
	for _, c := range tr.CertificateAuthorities {
		certs := c.CertChain.Certificates
		if len(certs) == 0 {
			continue
		}
		x := ca{roots: x509.NewCertPool(), intermediates: x509.NewCertPool(), validFor: c.ValidFor}
		for i, raw := range certs {
			cert, err := x509.ParseCertificate(raw.RawBytes)
			if err != nil {
				return nil, fmt.Errorf("trusted root: a CA certificate: %w", err)
			}
			if i == len(certs)-1 {
				x.roots.AddCert(cert)
			} else {
				x.intermediates.AddCert(cert)
			}
		}
		r.cas = append(r.cas, x)
	}
	if len(r.logs) == 0 || len(r.cas) == 0 {
		return nil, errors.New("trusted root: no usable transparency log or certificate authority")
	}
	return r, nil
}

// --- the bundle -----------------------------------------------------------------------

type bundle struct {
	MediaType            string `json:"mediaType"`
	VerificationMaterial struct {
		Certificate struct {
			RawBytes []byte `json:"rawBytes"`
		} `json:"certificate"`
		TlogEntries []tlogEntry `json:"tlogEntries"`
	} `json:"verificationMaterial"`
	DSSEEnvelope struct {
		Payload     []byte `json:"payload"`
		PayloadType string `json:"payloadType"`
		Signatures  []struct {
			Sig []byte `json:"sig"`
		} `json:"signatures"`
	} `json:"dsseEnvelope"`
}

type tlogEntry struct {
	LogIndex         string                 `json:"logIndex"`
	LogID            struct{ KeyID []byte } `json:"logId"`
	KindVersion      struct{ Kind, Version string }
	IntegratedTime   string `json:"integratedTime"`
	InclusionPromise struct {
		SignedEntryTimestamp []byte `json:"signedEntryTimestamp"`
	} `json:"inclusionPromise"`
	InclusionProof struct {
		LogIndex   string   `json:"logIndex"`
		RootHash   []byte   `json:"rootHash"`
		TreeSize   string   `json:"treeSize"`
		Hashes     [][]byte `json:"hashes"`
		Checkpoint struct {
			Envelope string `json:"envelope"`
		} `json:"checkpoint"`
	} `json:"inclusionProof"`
	CanonicalizedBody []byte `json:"canonicalizedBody"`
}

// rekorDSSE is the body Rekor logs for a dsse entry, version 0.0.1.
type rekorDSSE struct {
	APIVersion string `json:"apiVersion"`
	Kind       string `json:"kind"`
	Spec       struct {
		PayloadHash struct{ Algorithm, Value string } `json:"payloadHash"`
		Signatures  []struct {
			Signature string `json:"signature"`
			Verifier  string `json:"verifier"`
		} `json:"signatures"`
	} `json:"spec"`
}

type statement struct {
	Type          string `json:"_type"`
	PredicateType string `json:"predicateType"`
	Subject       []struct {
		Name   string            `json:"name"`
		Digest map[string]string `json:"digest"`
	} `json:"subject"`
}

// Verify checks one Sigstore bundle against digest (a SHA-256) and the policy.
func (r *Root) Verify(bundleJSON []byte, digest [32]byte, p Policy) error {
	var b bundle
	if err := json.Unmarshal(bundleJSON, &b); err != nil {
		return fmt.Errorf("not a bundle: %w", err)
	}
	if b.MediaType != "application/vnd.dev.sigstore.bundle.v0.3+json" {
		return fmt.Errorf("bundle media type %q", b.MediaType)
	}
	if len(b.VerificationMaterial.TlogEntries) != 1 {
		return fmt.Errorf("%d transparency log entries, want 1", len(b.VerificationMaterial.TlogEntries))
	}
	entry := b.VerificationMaterial.TlogEntries[0]
	if entry.KindVersion.Kind != "dsse" || entry.KindVersion.Version != "0.0.1" {
		return fmt.Errorf("log entry kind %s %s, want dsse 0.0.1", entry.KindVersion.Kind, entry.KindVersion.Version)
	}
	integrated, err := strconv.ParseInt(entry.IntegratedTime, 10, 64)
	if err != nil {
		return fmt.Errorf("integrated time %q", entry.IntegratedTime)
	}
	when := time.Unix(integrated, 0).UTC()

	// The certificate, at the time Rekor saw it.
	leaf, err := x509.ParseCertificate(b.VerificationMaterial.Certificate.RawBytes)
	if err != nil {
		return fmt.Errorf("certificate: %w", err)
	}
	if err := r.chain(leaf, when); err != nil {
		return err
	}
	if err := identity(leaf, p); err != nil {
		return err
	}
	key, ok := leaf.PublicKey.(*ecdsa.PublicKey)
	if !ok {
		return errors.New("certificate key is not ECDSA")
	}

	// The envelope, and what it says.
	env := b.DSSEEnvelope
	if env.PayloadType != dssePayload || len(env.Signatures) != 1 {
		return fmt.Errorf("envelope: payload type %q, %d signatures", env.PayloadType, len(env.Signatures))
	}
	if !ecdsaVerify(key, pae(env.PayloadType, env.Payload), env.Signatures[0].Sig) {
		return errors.New("the envelope's signature does not verify with the certificate")
	}
	var st statement
	if err := json.Unmarshal(env.Payload, &st); err != nil {
		return fmt.Errorf("statement: %w", err)
	}
	if st.Type != statementV1 || st.PredicateType != predicateSLSA {
		return fmt.Errorf("statement %s of %s, want SLSA provenance v1", st.Type, st.PredicateType)
	}
	want := hex.EncodeToString(digest[:])
	found := false
	for _, s := range st.Subject {
		if s.Digest["sha256"] == want {
			found = true
		}
	}
	if !found {
		return fmt.Errorf("no subject has sha256 %s", want)
	}

	// The log entry: for this envelope, promised and included.
	if err := sameEntry(entry.CanonicalizedBody, env.Payload, env.Signatures[0].Sig, leaf); err != nil {
		return err
	}
	return r.logged(entry, when)
}

func (r *Root) chain(leaf *x509.Certificate, when time.Time) error {
	var last error = errors.New("no certificate authority was valid then")
	for _, c := range r.cas {
		if !c.validFor.covers(when) {
			continue
		}
		_, err := leaf.Verify(x509.VerifyOptions{
			Roots:         c.roots,
			Intermediates: c.intermediates,
			CurrentTime:   when,
			KeyUsages:     []x509.ExtKeyUsage{x509.ExtKeyUsageCodeSigning},
		})
		if err == nil {
			return nil
		}
		last = err
	}
	return fmt.Errorf("certificate does not chain to Fulcio at %s: %w", when.Format(time.RFC3339), last)
}

func identity(leaf *x509.Certificate, p Policy) error {
	san := ""
	for _, u := range leaf.URIs {
		san = u.String()
	}
	if len(leaf.URIs) != 1 || san != p.Workflow {
		return fmt.Errorf("signed by %q, not %s", san, p.Workflow)
	}
	for _, ext := range leaf.Extensions {
		if ext.Id.Equal(oidIssuerV2) {
			var issuer string
			if _, err := asn1.Unmarshal(ext.Value, &issuer); err != nil {
				return fmt.Errorf("OIDC issuer extension: %w", err)
			}
			if issuer != oidcIssuer {
				return fmt.Errorf("OIDC issuer %q, not %s", issuer, oidcIssuer)
			}
			return nil
		}
	}
	return errors.New("certificate names no OIDC issuer")
}

// pae is DSSE's pre-authentication encoding, what the signature covers.
func pae(payloadType string, payload []byte) []byte {
	return []byte(fmt.Sprintf("DSSEv1 %d %s %d %s", len(payloadType), payloadType, len(payload), payload))
}

func ecdsaVerify(key *ecdsa.PublicKey, msg, sig []byte) bool {
	h := sha256.Sum256(msg)
	return ecdsa.VerifyASN1(key, h[:], sig)
}

func sameEntry(body, payload, sig []byte, leaf *x509.Certificate) error {
	var e rekorDSSE
	if err := json.Unmarshal(body, &e); err != nil {
		return fmt.Errorf("log entry body: %w", err)
	}
	ph := sha256.Sum256(payload)
	if e.Kind != "dsse" || e.Spec.PayloadHash.Algorithm != "sha256" || e.Spec.PayloadHash.Value != hex.EncodeToString(ph[:]) {
		return errors.New("the log entry is for another payload")
	}
	if len(e.Spec.Signatures) != 1 || e.Spec.Signatures[0].Signature != base64.StdEncoding.EncodeToString(sig) {
		return errors.New("the log entry is for another signature")
	}
	verifier, err := base64.StdEncoding.DecodeString(e.Spec.Signatures[0].Verifier)
	if err != nil {
		return errors.New("the log entry's verifier is not base64")
	}
	block, _ := pem.Decode(verifier)
	if block == nil || !bytes.Equal(block.Bytes, leaf.Raw) {
		return errors.New("the log entry is for another certificate")
	}
	return nil
}

func (r *Root) logged(e tlogEntry, when time.Time) error {
	logID := hex.EncodeToString(e.LogID.KeyID)
	lk, ok := r.logs[logID]
	if !ok || !lk.validFor.covers(when) {
		return fmt.Errorf("the entry is in log %s, which the trusted root does not vouch for then", logID)
	}
	index, err := strconv.ParseInt(e.LogIndex, 10, 64)
	if err != nil {
		return fmt.Errorf("log index %q", e.LogIndex)
	}
	integrated, _ := strconv.ParseInt(e.IntegratedTime, 10, 64)

	// The signed entry timestamp: Rekor's promise, over its canonical JSON (keys sorted).
	set, _ := json.Marshal(struct {
		Body           string `json:"body"`
		IntegratedTime int64  `json:"integratedTime"`
		LogID          string `json:"logID"`
		LogIndex       int64  `json:"logIndex"`
	}{base64.StdEncoding.EncodeToString(e.CanonicalizedBody), integrated, logID, index})
	if !ecdsaVerify(lk.key, set, e.InclusionPromise.SignedEntryTimestamp) {
		return errors.New("the signed entry timestamp does not verify with Rekor's key")
	}

	// The inclusion proof, up to a root Rekor signed in a checkpoint.
	p := e.InclusionProof
	pIndex, err1 := strconv.ParseUint(p.LogIndex, 10, 64)
	size, err2 := strconv.ParseUint(p.TreeSize, 10, 64)
	if err1 != nil || err2 != nil {
		return errors.New("inclusion proof: index or tree size is not a number")
	}
	leafHash := sha256.Sum256(append([]byte{0}, e.CanonicalizedBody...))
	root, err := rootFromInclusion(pIndex, size, leafHash[:], p.Hashes)
	if err != nil {
		return fmt.Errorf("inclusion proof: %w", err)
	}
	if !bytes.Equal(root, p.RootHash) {
		return errors.New("inclusion proof: does not lead to its root hash")
	}
	return checkpoint(p.Checkpoint.Envelope, size, root, lk.key)
}

// rootFromInclusion is RFC 9162's inclusion proof verification (section 2.1.3.2).
func rootFromInclusion(index, size uint64, leaf []byte, proof [][]byte) ([]byte, error) {
	if index >= size {
		return nil, fmt.Errorf("index %d outside a tree of %d", index, size)
	}
	node := func(l, r []byte) []byte {
		h := sha256.New()
		h.Write([]byte{1})
		h.Write(l)
		h.Write(r)
		return h.Sum(nil)
	}
	fn, sn, r := index, size-1, leaf
	for _, p := range proof {
		if sn == 0 {
			return nil, errors.New("longer than the tree is deep")
		}
		if fn&1 == 1 || fn == sn {
			r = node(p, r)
			for fn&1 == 0 && fn != 0 {
				fn >>= 1
				sn >>= 1
			}
		} else {
			r = node(r, p)
		}
		fn >>= 1
		sn >>= 1
	}
	if sn != 0 {
		return nil, errors.New("shorter than the tree is deep")
	}
	return r, nil
}

// checkpoint verifies a signed note: "<origin>\n<size>\n<base64 root>\n[...]\n\n" then
// "— <name> <base64(4-byte key hint || signature)>\n" lines, one of which must be Rekor's.
func checkpoint(envelope string, size uint64, root []byte, key *ecdsa.PublicKey) error {
	text, sigs, ok := strings.Cut(envelope, "\n\n")
	if !ok {
		return errors.New("checkpoint: not a signed note")
	}
	text += "\n"
	lines := strings.Split(strings.TrimSuffix(text, "\n"), "\n")
	if len(lines) < 3 {
		return errors.New("checkpoint: too short")
	}
	if lines[1] != strconv.FormatUint(size, 10) || lines[2] != base64.StdEncoding.EncodeToString(root) {
		return errors.New("checkpoint: not for the proof's tree")
	}
	for _, line := range strings.Split(strings.TrimSuffix(sigs, "\n"), "\n") {
		f := strings.Fields(strings.TrimPrefix(line, "— "))
		if len(f) != 2 {
			continue
		}
		raw, err := base64.StdEncoding.DecodeString(f[1])
		if err != nil || len(raw) < 5 {
			continue
		}
		if ecdsaVerify(key, []byte(text), raw[4:]) {
			return nil
		}
	}
	return errors.New("checkpoint: no signature verifies with Rekor's key")
}

// --- fetching -------------------------------------------------------------------------

// API is GitHub's REST API; Fetch asks it for a digest's attestations (no token needed
// for a public repository).
const API = "https://api.github.com"

// Fetch returns the bundles GitHub holds for digest in repo.
func Fetch(c *http.Client, api, repo string, digest [32]byte) ([]json.RawMessage, error) {
	url := fmt.Sprintf("%s/repos/%s/attestations/sha256:%s", api, repo, hex.EncodeToString(digest[:]))
	req, err := http.NewRequest(http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/vnd.github+json")
	resp, err := c.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNotFound {
		return nil, nil
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("%s: %s", url, resp.Status)
	}
	var out struct {
		Attestations []struct {
			Bundle json.RawMessage `json:"bundle"`
		} `json:"attestations"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 16<<20)).Decode(&out); err != nil {
		return nil, fmt.Errorf("%s: %w", url, err)
	}
	bundles := make([]json.RawMessage, 0, len(out.Attestations))
	for _, a := range out.Attestations {
		bundles = append(bundles, a.Bundle)
	}
	return bundles, nil
}

// VerifyAny passes when one of bundles verifies; otherwise it says why each did not.
// Bundles of another kind (GitHub's own release attestation, signed by its internal
// Fulcio with no Rekor entry) are among those that do not, which is fine.
func (r *Root) VerifyAny(bundles []json.RawMessage, digest [32]byte, p Policy) error {
	if len(bundles) == 0 {
		return fmt.Errorf("no attestation for sha256:%s", hex.EncodeToString(digest[:]))
	}
	var why []string
	for i, b := range bundles {
		err := r.Verify(b, digest, p)
		if err == nil {
			return nil
		}
		why = append(why, fmt.Sprintf("bundle %d: %v", i+1, err))
	}
	return fmt.Errorf("no attestation verifies (%s)", strings.Join(why, "; "))
}
