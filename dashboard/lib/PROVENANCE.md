# Vendored libraries

| File | Version | Source | SHA-256 |
|------|---------|--------|---------|
| `marked.min.js` | 12.0.2 | https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js | `15fabce5b65898b32b03f5ed25e9f891a729ad4c0d6d877110a7744aa847a894` |

Vendored to avoid a runtime CDN dependency. To verify:

```bash
shasum -a 256 dashboard/lib/marked.min.js
```
