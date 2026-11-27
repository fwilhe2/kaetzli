# Auditability

**Status: not implemented.** A review, as of 2026-11-27, of what a corporate audit of
kaetzli would find, and what would make one easier. Nothing below has been changed yet.

The frame is what corporate standards usually end up citing for software they build or
adopt: change management (ISO 27001 A.8.32, SOC 2 CC8), NIST's Secure Software Development
Framework (SP 800-218), SLSA for build provenance and, where the product is sold in the EU,
the Cyber Resilience Act's vulnerability reporting (from September 2026).

## What already holds up

Most of the technical evidence exists, and is generated rather than asserted:

| An auditor asks for | kaetzli has |
| --- | --- |
| Software bill of materials | SPDX 2.3 from the build's own records, not a scan (`image/sbom.py`, `test/check-sbom.py`), per image flavour and per extension |
| Vulnerability management | grype and govulncheck on every build, OpenVEX with one checkable reason per `not_affected` (`test/cve-not-affected.txt`), CVE history and context (EPSS, KEV) |
| Build provenance | `actions/attest` on pushed images, SBOMs and bundle files; actions pinned by SHA; package compiles with `--network=none`; inputs pinned by SHA256 and, where upstream signs, by signature |
| Reproducibility | the `reproducible` job (two assemblies, equal bytes), `tools/rebuild-release.py` weekly with an attestation of the result |
| Licence compliance | SPDX `LICENSE=` checked against `test/dfsg-licenses.txt`, licence texts shipped and guarded, a GPL source offer (`tools/corresponding-source.py`) |
| Runtime integrity | EROFS root on dm-verity, a UKI signable for Secure Boot, verified extensions, no passwords, nothing setuid |
| Hardening | `test/check-hardening.py` per binary, `test/kernel-hardening.py` (KSPP, lockdown) |
| Secrets in the repository | GitHub secret scanning and push protection are on |

## Gaps

1. **Change control is a convention, not enforced.** The `main` ruleset blocks deletion and
   force pushes only: no required pull request, review or status check, so a direct push
   lands without CI having passed. `result` is "the one required check" only by
   agreement. `tools/auto-merge.py` merges bot pull requests with no human review; its
   scope (one package's `env.sh` and `patches/upstream/`, never a key) makes that a
   defensible pre-approved change, but it is not written down as one.
2. **Commits and tags are unsigned.** Neither people's commits nor the bots' carry a
   verifiable signature, so authorship rests on GitHub's account records alone.
3. **Some inputs are trust on first use.** 24 packages have no `SIG_URL`: aardvark-dns,
   ca-certificates, cni-plugins, conmon, cri-o, cri-tools, e2fsprogs, glib, iana-etc,
   iproute2, iptables, jq, json-c, kernel, kubernetes, libbpf, libcap, netavark, passt,
   podman, socat, systemd, tzdata, util-linux. Several upstreams do sign (kernel.org's
   `.tar.sign` for the kernel, util-linux, e2fsprogs, iproute2, libcap; netfilter.org;
   systemd's signed tags); for the rest the SHA256 is the pin and that is the record.
4. **No security policy.** No `SECURITY.md`: no private reporting channel, no statement of
   what is supported (the release model's answer, "the latest snapshot; no backports",
   `docs/proposals/release.md`) or how fast a fix ships. Dependabot security updates are
   off (version updates are on).
5. **Evidence expires.** Actions artifacts (test logs, consoles, scan reports) go after the
   retention period, snapshots after 37 days. Audits look back a year or more, and need to
   show what was shipped *then* and how it was tested.
6. **Nothing maps controls to evidence.** The mechanisms are spread over `CLAUDE.md`, the
   guides and the workflows. Finding them is most of an auditor's time.

## Proposal, cheapest first

1. **Enforce change control on `main`**: the ruleset requires a pull request and the
   `result` check; signed commits if (2) is taken up. Bots open pull requests already, so
   auto-merge is unaffected. The cost is that nobody pushes to `main` directly any more,
   the maintainer included.
2. **A controls document** (`docs/guides/controls.md`): one row per control, the mechanism, and
   the file or job that is its evidence, against SSDF practices and SLSA levels. It states
   auto-merge as the standard change and its limits, and the release model as the support
   policy. Kept true like the rest: a check that a named file or job exists.
3. **`SECURITY.md`**: GitHub's private vulnerability reporting, the supported version (the
   latest snapshot), a target time from report to a fixed snapshot.
4. **An evidence archive per release**: SBOMs, VEX, scan results, test summaries, the
   kernel configuration diff, attestations and a licence report, as one signed bundle kept
   beyond the snapshot's expiry (a long-lived release, or an attested OCI artifact in the
   registry). `tools/release.py` collects most of it already.
5. **Close the signature gaps** where upstream signs (`SIG_URL` and a pinned key each),
   and list the rest with their reason.
6. **OpenSSF Scorecard**: one workflow, an external score auditors recognise, and a check
   that the above stays true.

## Confidentiality: how the image is run

These are questions about a deployment, not the repository, and are unverified here:

- **Encryption at rest**: the root holds no secrets, but a persistent `/var` disk does.
  Whether it can be encrypted (and keyed by TPM or credential) needs checking.
- **Audit and log forwarding**: whether kernel audit is configured in, and how the journal
  reaches a central collector (`systemd-journal-upload` or an agent in an extension).
- **Rebuilding offline**: the sources image and `downloads/` come close to a build from
  internal mirrors only; what still reaches the internet (registries, toolchains) should
  be listed.
- **FIPS**, only where a standard requires it: OpenSSL 3.5 has a FIPS provider; whether it
  is built and shipped is unchecked.
