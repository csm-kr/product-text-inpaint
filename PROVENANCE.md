# Source and scope

The original ink extraction helper and minimal Python inpainting modules were
separated from `csm-kr/god-tibo-image-lab` at commit
`03bc501a5851f7e2ccfce168c4ef479ec8097ce9`. The MIT license is retained.

Only the crop/padding, local blend, geometry, inpaint prompt, authentication check
and image runner needed for cleanup are included. This is a fresh repository;
it does not carry the original project's Git history, studio or other editing
interfaces. No access to that source repository is required to install or run.

The three bundled Node modules and their public npm dependency are described in
[the runtime provenance](vendor/god-tibo-gpt-image2-skill/PROVENANCE.md).

The new cleanup/restoration CLI, installed-skill runner, instructions and
installation marker connect this independent package to any photo workspace.
Images and comparative metrics come from the completed product image tests.
Remote AI generation is separate from local sample reproduction and automated
tests; their outcomes are reported separately.
