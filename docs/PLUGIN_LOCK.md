# Plugin SHA-256 lock

Every entry in `vault-template/plugins.json` pins a release tag and a
`sha256` lock covering each asset that release ships:

```json
{
  "id": "dataview",
  "repo": "blacksmithgu/obsidian-dataview",
  "version": "0.5.68",
  "sha256": {
    "manifest.json": "<64 lowercase hex>",
    "main.js": "<64 lowercase hex>",
    "styles.css": "<64 lowercase hex>"
  }
}
```

`manifest.json` and `main.js` are required. Include `styles.css` only if the
release has one. If there's no `styles.css` entry, the provisioner never
downloads that file.

## What the provisioner enforces

- When it loads the lock, an entry with no `sha256`, a missing required
  asset, an unknown asset name, or a digest that isn't 64 lowercase hex
  chars stops provisioning before the vault is touched.
- At install time, it downloads every locked asset and hashes it before
  writing anything. If a file is missing or its digest doesn't match, that
  plugin fails: none of its files are written and it isn't enabled. The
  other plugins still install and get enabled.
- Plugins already on disk at the pinned version count as `ok` and aren't
  hashed again.

## Updating a pin

1. Pick the new release tag. The `version` in its `manifest.json` must
   match the tag.
2. Record the provenance. See whether GitHub publishes digests and whether
   any asset was replaced after the release went out (an asset
   `updated_at` later than `published_at` means it was re-uploaded):

   ```bash
   repo=blacksmithgu/obsidian-dataview; tag=0.5.68
   gh api "repos/$repo/releases/tags/$tag" --jq '.published_at as $p | .assets[]
     | select(.name | IN("manifest.json","main.js","styles.css"))
     | "\(.digest // "none") \(.name) updated=\(.updated_at) published=\($p)"'
   ```

3. Compute the digests yourself from the URL the provisioner downloads:

   ```bash
   for f in manifest.json main.js styles.css; do
     curl -fsSL "https://github.com/$repo/releases/download/$tag/$f" \
       | sha256sum | sed "s|-|$f|"
   done
   ```

   If `styles.css` returns 404, leave it out of the lock. If GitHub
   publishes a digest, it must equal yours. If it doesn't, don't pin.

4. Update `version` and `sha256` in `plugins.json`. In the PR, say for each
   asset whether GitHub's digest matched or the value is only computed.
5. A second maintainer re-verifies the whole lock independently, using the
   command below, before approving.
6. Provision a throwaway vault and confirm `errors` is `{}` and
   `--check` reports `ok` for every plugin.

## Verifying the whole lock

```bash
jq -r '.[] | .repo as $r | .version as $v | .sha256 | to_entries[]
  | "\(.value) \($r) \($v) \(.key)"' vault-template/plugins.json |
while read -r want repo tag file; do
  got=$(curl -fsSL "https://github.com/$repo/releases/download/$tag/$file" | sha256sum | cut -d' ' -f1)
  if [ "$got" = "$want" ]; then echo "OK   $repo $tag $file"; else echo "FAIL $repo $tag $file got $got"; fi
done
```

Every line should start with `OK`.
