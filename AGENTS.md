# Project operating rules

- Keep all authored implementation, build utilities and tests under `src/`.
- Preserve blank lines between logical stages inside functions. A formatter
  handles wrapping. Semantic spacing still requires human review.
- The shared `winix_local` library must import without Home Assistant installed.
- The HACS build bundles shared modules from their single source into `_core/`.
  Never edit or commit generated runtime copies.
- Root `manifest.json` is canonical HA metadata. HACS requires it at the root
  with `content_in_root: true`. The builder copies it into the installation ZIP.
- Do not publish, push, deploy, restart live services, provision a real device,
  or change router settings unless explicitly authorized.
- Never commit installation bundles, credentials, private keys, packet captures,
  personal network details or controller state.
- Keep verified device support distinct from inherited catalog metadata.
- Run `make validate` before committing. Verify the built ZIP independently of
  the checkout when changing packaging or integration imports.
