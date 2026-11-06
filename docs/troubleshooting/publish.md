# Troubleshooting: publishing a LoRA by hand

The release kit (`kiln release`, session 2) builds and verifies a package; **the operator alone uploads it, by
hand**, to a model-sharing site. Rows below were hit while preparing and uploading sfw LoRA versions in 2026;
a site's screens and rules change often, so recheck them before each upload. Anything else:
[docs/STUCK.md](../STUCK.md).

## The rules (never broken)

1. No tool and no agent uploads, posts, logs in, drives a browser or uses the site's API or MCP server to
   post. Building and verifying the package are the only automated steps; afterwards a tool may only read
   the public API.
2. Upload only after the operator's explicit go for that version, in their own words, written into the
   package's checklist. "Build the package" is not a go.
3. sfw versions only, on the site's sfw side; never an nsfw or suggestive image.
4. Upload only files of a package that `verify` called clean. An original render or a checkpoint from the
   training folder carries its graph, local paths, run ids and the dataset's caption statistics.
5. Only the operator judges the showcase images. Agents never open an image or a window.
6. Every decision (names, disclosure, monetisation, permissions) is the operator's, recorded with their words.

## Build and verify

| symptom | cause | action |
|---|---|---|
| the scan finds a network path in a real LoRA header | the trainer stores caption statistics as JSON text inside the JSON header; an escaped non-ASCII character got its backslash doubled when the header was written again | scan the strings themselves with nested JSON opened; that also catches a name written as an escape |
| a 3-byte pattern such as a drive prefix turns up in pixel data | compressed pixels and tensors match short patterns by chance and carry no readable text | the scan skips pixels and tensors and searches every other byte |
| the card stays a draft | the version has no changelog entry | one changelog entry per version |
| a field reads `null` | `null` means "not known yet" | fill it from the operator's numbers; it never hides a value the tool computes |
| the dry run said release-ready, the real build crashed | the dry run does not render every file of the package | run the real build well before upload day, not only the dry run |
| the LoRA is fp32 | some trainers save fp32 by default; twice the size | the tool warns and never converts; the operator decides |
| the build refuses the base model | the base's licence forbids derivatives, is unknown, or forbids relicensing while the LoRA's licence differs | read the base's own model card; the family summary is a fallback for foundation bases only |
| the card's training-image count is wrong | the count was read from another registry than the run's frozen snapshot | count from the snapshot of the run's dataset version |
| someone edited a package by hand | any hand change breaks what `verify` checked | rebuild and verify again; the operator's go in the checklist is the one hand entry |
| a rebuild refuses to overwrite | a version is rebuilt only with `--replace`; the old folder is kept | removing the old folder is the operator's call |

## Public text and the site's scanners

| symptom | cause | action |
|---|---|---|
| advice in the card trips the site's minor audit | a negated word still contains the word ("no X" contains X) | write positive guidance (prompt the subject as an adult, in adult settings and poses); audit the whole rendered card, not only the prompts |
| a prompt is flagged although the subject is an adult | the audit flags a youth adjective next to a person noun within a comma-free run of words | state the adult class phrase plainly; the card says the subject's adult age |
| a negative prompt is read as a minor signal | the audit reads words about adult bodies in a negative that way | keep such words out of negatives |
| a base model's licence shows differently on the site | the site's own registry can mislabel a base's licence | the card and the licence file state the licence from the base's own card |

## Upload screens (the operator, by hand; observed 2026)

| symptom | cause | action |
|---|---|---|
| pasted Markdown shows `##` and `**` literally | the site's fields are rich-text editors | copy rendered Markdown (from a preview), not the source; tables do not paste: use lists; inline code instead of fenced blocks |
| some commercial-use boxes are greyed out and forced on | the boxes are ranked; the top one forces the ones below | untick the top box first, then set the rest |
| several versions on one page: which permissions? | one page covers all versions | the strictest base licence among the versions sets the page's permissions |
| a version shows the wrong base model | the wizard pre-selects a default base | set the base model explicitly for every version |
| the upload screen shows no hash | the hash is shown only after publishing | after publishing, a read-only check compares the site's SHA256 with the package's `.sha256` |
| images disappear for logged-out visitors | every image gets a rating badge; logged-out visitors see PG only | the cover is PG; remove every image rated R or higher; keep enough images after that |
| images in a low-coverage outfit are rated R, waist-up shots included | the site rates by visible coverage, not by framing | build the showcase on fuller outfits |
| an image credits a checkpoint that carries an adult rating | the site links resources by hash | observed: the credit did not raise the image's rating; each image is rated on its own |
| the public API returns empty image metadata right after posting | the metadata appears later | match the live images to the showcase files by thumbnail |
| reading comments on a post | the site's terms allow automation only through its public API, its MCP server or its own software | not supported; the operator relays feedback by hand (`easel lessons add --source civitai --finding "<...>" --evidence "<...>" --rule "<...>"`) |

An agent may guide the operator through the screens in chat, one screen at a time, reading every value from
the package's settings file; it never touches the site.
