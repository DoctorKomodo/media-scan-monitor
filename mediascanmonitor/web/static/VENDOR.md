# Vendored third-party assets

## htmx

- **Package:** htmx.org
- **Version:** 2.0.10
- **Source:** https://unpkg.com/htmx.org@2.0.10/dist/htmx.min.js
- **SHA-256:** 71ea67185bfa8c98c39d31717c6fce5d852370fcdfd129db4543774d3145c0de
- **SHA-384 (SRI):** sha384-H5SrcfygHmAuTDZphMHqBJLc3FhssKjG7w/CeCpFReSfwBWDTKpkzPP8c+cLsK+V
- **Vendored:** 2026-06-21
- **Why vendored:** Container runs without outbound internet (media mounts are local); CDN
  dependency would break the UI offline and introduce an external trust anchor / CSP exposure.
