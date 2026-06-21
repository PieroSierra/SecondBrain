# Vendored libraries

| File | Version | Source | SHA-256 |
|------|---------|--------|---------|
| `marked.min.js` | 12.0.2 | https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js | `15fabce5b65898b32b03f5ed25e9f891a729ad4c0d6d877110a7744aa847a894` |
| `purify.min.js` | 3.2.4 | https://cdn.jsdelivr.net/npm/dompurify@3.2.4/dist/purify.min.js | `8eb41b658831fab175fad9bcd00fcb2d84e0ed3a25a55053d4ecd4444b8b43a0` |

Vendored to avoid a runtime CDN dependency. To verify:

```bash
shasum -a 256 dashboard/lib/marked.min.js
```
