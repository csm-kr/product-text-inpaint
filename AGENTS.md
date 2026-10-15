# Agent instructions

- This repository contains product image cleanup and original text/logo restoration.
- Read `skills/product-text-inpaint/SKILL.md` for image work.
- Run the local `text-clean` launcher (`text-clean.cmd` on Windows).
  Setup is `setup-local.sh` / `setup-local.cmd`; tests use `test-local`.
- Credentials stay in each user's Codex home. Never copy them into the repository.
- This project is independent of other repositories. Do not fetch a private dependency.
- Keep Windows commands in a PTY and hide background child windows.
- Preserve original files, canvas size and pixels outside specified edit regions.
- A successful pixel check does not prove correct extraction, wording or visual quality.
