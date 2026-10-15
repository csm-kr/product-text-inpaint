# Bundled image runtime

Source: `csm-kr/god-tibo-gpt-image2-skill`, commit
`3f10c8dc71ea6ba58d2f0119afcb41417d5490dd`.

Only the three runtime modules and the npm package manifests are bundled. The
original MIT license is preserved in [LICENSE](LICENSE). Installation does not
clone the source repository or require its access permission.

Local changes carried into this distribution:

- `media-tools.mjs`: hide Windows child process windows while retaining output.
- `private-codex-client.mjs`: include the sanitized backend error message in an
  HTTP failure instead of returning only its status code.

The lockfile pins the public npm dependency `god-tibo-imagen` to `0.3.1`.
Codex authentication is read from each user's own Codex home; credentials are
not distributed. This uses an unsupported private backend and can stop working
when its contract changes.
