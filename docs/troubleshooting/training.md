# Troubleshooting: training

Every row below was hit, diagnosed and fixed once; the failure message rarely points at the cause (a byte-order
mark looks like a syntax error, an encoding bug like a trainer crash). Numbers are rounded, from runs on an 8 GB
NVIDIA GPU under Windows 11 with kohya sd-scripts v0.12.3 (2026-11); on other hardware match the shape, not the
numbers. Find the symptom, do the action once, record it (AGENTS.md section 6). Anything else:
[docs/STUCK.md](../STUCK.md).

**Who acts.** An action that starts with `maintainer:` is a change to the trainer wrapper or to kiln's code,
and one that starts with `operator:` is the operator's to do. Your part of either is to report the symptom and
the row, with `need: maintainer` or `need: operator` (STUCK.md). You never start, stop, restart or suspend a
training or render process: GPU work is submitted, and the operator or the scheduler runs it.

**Rule zero.** A flag that raises no error is not a flag that does what you think. Check the code path, and
check which line the trainer's log prints for it.

## The 8 GB recipe, and why each flag is there

The family files (`families/<family>.yaml`, `training:`) carry these values; this table is the reason.

| flag | reason |
|---|---|
| `--mixed_precision bf16` | halves activation memory; fp16 risks overflow on SDXL |
| `--gradient_checkpointing` | required at 8 GB; trades about 30% speed for a large memory drop |
| `--optimizer_type AdamW8bit` | optimizer state at rank 32 does not fit in fp32 |
| `--fp8_base_unet` | the base UNet in fp8 is what makes 1024 fit in 8 GB with room; see the quality check at the end |
| `--gradient_accumulation_steps 2` | effective batch 2 from batch size 1; 4 doubled the wall clock for little gain |
| `--network_train_unet_only` | training the text encoders too pushes SDXL past 8 GB |
| `--cache_latents` (in RAM, not to disk) | caching to disk wrote latent files into the frozen dataset folder, reused later by runs on bases with another VAE; caching in RAM is quick |
| `--cache_text_encoder_outputs` | the text encoders leave the GPU after caching (needs caption shuffle and caption dropout off) |
| `--max_token_length 150` | the default 75 silently cut most sentence captions |
| `--min_snr_gamma 5` | steadier loss across timesteps |
| `--save_precision fp16` | about 170 MB per rank-32 SDXL checkpoint instead of twice that in fp32 |
| a streamed base-model loader (the low-memory wrapper) | the stock loader needed several times the system memory the trainer uses afterwards; the wrapper also fixes `--resume` (below) |
| no in-loop sampling | sampling competes with the training graph; sample after training |
| `--sdpa` only | `--mem_eff_attn` overrides it when both are passed; xformers is not assumed |

**Never:** an absolute path in a launcher or dataset template (the renderer exits 3 when a rendered file
contains one); training without a frozen, materialised snapshot; reusing a generated file after a template
changed (render again); comparing checkpoints trained with another rank, resolution or dataset.

## Memory

What a job needs in free host RAM before it starts is in each family file (`host_ram_gb.train`,
`host_ram_gb.eval`; `eval_includes_comfyui_server` says whether the ComfyUI server is counted). Roughly, on an
8 GB card: SDXL training with the streamed loader holds a few GB of host RAM and most of the VRAM; an SDXL eval
render through ComfyUI needs more host RAM than training, most of it while a checkpoint loads.

Why the streamed loader: the stock loader (fp32 UNet) raised the commit charge far past the free RAM within
seconds; Windows charges every GPU allocation to commit too, so it failed with "CUDA error: out of memory"
while the GPU was empty. `--full_bf16` still left almost nothing free and also casts the LoRA to bf16.
`safetensors.safe_open` read the whole file on open on this platform. Reading each tensor from its offset,
converting and freeing it, with the UNet straight into fp8, streamed onto the GPU, and the text encoders freed
after caching, kept the trainer at a few GB of host RAM.

`--fp8_base_unet` at 1024, rank 32, batch 1: several times faster per optimizer step than without it, which
ran the VRAM to nearly full. Progress bars count optimizer steps (batch x accumulation images), not images.

## Launcher and flag traps

| symptom | cause | action |
|---|---|---|
| a run takes about 3x the planned time | both `--max_train_steps` and `--max_train_epochs` passed: epochs win, and the cosine schedule stretches | pass steps only |
| the log says "Enable memory efficient attention" although sdpa was asked | `--mem_eff_attn` and `--sdpa` both passed: mem_eff wins | sdpa only; read which "Enable ..." line the log prints |
| captions lose their ending, no warning | the default 75-token limit | `--max_token_length 150` (or 225); count tokens before training (below) |
| sentence captions come out scrambled | `shuffle_caption` splits on commas | off for natural-language captions; it is for tag lists |
| text-encoder caching asserts at start | caption shuffle or caption dropout is on | both off when caching text-encoder outputs |
| a dropout default copied from another backend | defaults do not port across model families | set caption dropout per family; 0 for SDXL here |
| "training venv missing" from a rendered launcher | the launcher counted folder levels; the rendered copy sits one folder deeper | maintainer: find the root by searching upward for a marker file, never by counting levels |
| `--text_encoder_lr1` is rejected | a doc example names flags that do not exist | `--text_encoder_lr` |
| `--clip_skip 2` has no effect on SDXL training | SDXL training ignores it (a warning) | leave it out of training; it is an inference setting |
| `--persistent_data_loader_workers` raises | it needs at least one worker | drop it, or set workers above 0 |
| a v-pred base trains to noise | v-prediction needs its own flags | check the checkpoint header for `v_pred`; then `--v_parameterization --zero_terminal_snr` |
| `--sample_every_n_steps 0` divides by zero at step 1 | the trainer samples whenever the flag is present | pass the flag only when the interval is above 0 |
| a sample-prompt line renders hundreds of steps | in sd-scripts prompt files `--s` is the step count, not the seed (`--d` is the seed) | fix the prompt file before sampling |
| never use `--lowram` to fix an out-of-memory | it loads the models onto the GPU first; on 8 GB the driver then spills into system memory | use the streamed loader |

## Dataset config traps

| symptom | cause | action |
|---|---|---|
| `extra keys not allowed @ data['general']['seed']` | a dataset config allows only `general` and `datasets` | hyperparameters go on the command line, the dataset shape into the dataset config |
| the run "trained on the wrong data" | `--train_data_dir` is ignored when `--dataset_config` is given | the path comes from `image_dir` inside the config |
| a config key fails validation | `seed`, `max_data_loader_n_workers`, `persistent_data_loader_workers`, `no_half_vae` are command-line only | move them to the launcher |
| `--sdxl` or `--weight_decay` is rejected | neither flag exists | the SDXL trainer implies SDXL; `--optimizer_args "weight_decay=0.01"` |
| `pip install -r requirements.txt` fails with "does not appear to be a Python project" | the file ends with `-e .`, resolved against the current folder | operator: run it from inside the sd-scripts clone |

Validate a rendered dataset config with kohya's own schema before launching, from inside the sd-scripts
clone with the training interpreter:

```
python -c "import toml; from library.config_util import ConfigSanitizer as C; C(True, False, False, True).sanitize_user_config(toml.load('<rendered dataset toml>')); print('VALIDATED OK')"
```

## Memory, disk and other work on the machine

| symptom | cause | action |
|---|---|---|
| the run was paused or stopped for low RAM, low disk or heat | a guard or the scheduler on the machine acted on its own thresholds | report it with the line it printed; whether and when it resumes is the operator's call |
| the job waits a long time for RAM or disk | the scheduler waits until the job's needs (`host_ram_gb`, `limits.disk_floor_gb`) are free | expected; the operator's other work comes first; tell the operator if it lasts |
| free disk fell by tens of GB during a load | the page file grew; it shrinks when the process ends | check the disk floor after a run too, not only before |
| free disk falls while training, more than the run writes | another program writes to the same disk, for example a backup or sync tool copying each new checkpoint | measure file sizes and times at two moments before blaming anything; never touch the operator's files |
| the GPU runs hot on a long run | sustained load on a card with limited cooling | report it; a guard on the machine may pause the job; never change its thresholds |
| the eval step waits for RAM right after a family switch | the first check runs before the old server's memory is returned | expected; a later check passes |
| you are tempted to free memory | the memory belongs to other work on the machine | never free memory that belongs to other work: no cache drops, no touching other processes, services or agent sessions, no lowered thresholds; let the job wait and tell the operator |
| VRAM past full slows the run and eats host RAM, no error | the Windows driver spills into system memory | cap the trainer's GPU share (`train.max_vram_fraction`, operator's word) |

## Resume, saves and the queue

| symptom | cause | action |
|---|---|---|
| a resumed run saves step 600 as step 200 and never stops at max steps | sd-scripts v0.12.3 `--resume` restarts the step counter at 0 | resume only through the wrapper that fixes the counter; check the log: `load train state ...: {'current_epoch': <e>, 'current_step': <s>}`, the bar starting at `<s>/<max>`, the next save at `<s> + save_every` |
| `--skip_until_initial_step` skips about twice too many epochs | with accumulation it multiplies, then divides by optimizer steps per epoch | do not use it with gradient accumulation |
| the queue ran the next job, which failed the same way | a crash that is not a guard stop is a real failure; the queue does not retry it | report it; operator: hold the queue; maintainer: fix the code. The failed job is still untrained and runs first when the operator resumes the queue |
| the queue kept running a job after it was told to pause | the pid file names a shell; Python is its child, and the venv launcher adds another | report it; operator: pause the whole process tree before a maintainer edits code the queue will run next |
| saved states fill the disk | 0.2 to 0.5 GB each | keep the newest state of a finished run so it can be extended exactly; removing the rest is the operator's call |
| a documented command crashes on first use | it had never been run | maintainer: every command a doc tells an agent to run gets at least one test |

## Validation and sampling

| symptom | cause | action |
|---|---|---|
| `Cannot copy out of meta tensor`, minutes in | with a validation split the trainer caches text-encoder outputs twice; the encoders were freed after the first | maintainer: keep the encoders until the second caching call when any dataset block has `validation_split > 0` |
| validation loss scores against empty prompts | the holdout had no caption files | one caption file per holdout image; `kiln dataset check` counts them |
| MOSS-Image crashes at the first validation (`drop_cached_text_encoder_outputs() missing 2 required positional arguments`) | an sd-scripts v0.12.3 bug: validation runs each batch once per timestep, and the first pass writes back 2 of 4 tensors | maintainer: the wrapper passes a 2-tensor call through unchanged; training alone never shows it |
| validation adds many passes per run | the trainer also validates once per epoch by default | set a very large `--validate_every_n_epochs` beside `--validate_every_n_steps` |
| a retry redraws the step-0 control images | `--sample_at_first` on every attempt | drop it when every prompt already has a step-0 image |
| CUDA out of memory in the VAE decode of a sample | the fp32 VAE decoder does not fit next to the model | maintainer: decode in bf16 (about one 8-bit level of difference) |
| one sample that does not fit ends a run of hours | no guard around sampling | maintainer: catch out-of-memory per sample, log `sample image SKIPPED`, train on |
| MOSS-Image samples take very long | an official 1024 / 50-step sample is slow on an 8 GB card | sample at 768 / 30 steps for the eval grid |
| caching MOSS-Image captions runs out of VRAM | its 9B text encoder (7.4 GB in INT8) is loaded whole | maintainer: stream the encoder one layer at a time on the GPU |
| a stand-in module fails on a missing attribute minutes in | the trainer reads an attribute you did not expect | maintainer: before using a stand-in, grep the trainer for every attribute it reads |
| the reference inference script refuses the INT8 model | it merges the LoRA into the model, which the pre-quantized file does not allow | take eval images from the trainer's own sampler |
| triton made training slower | measured slower per step at 768 with it than without | leave triton out on this class of GPU |
| `@triton.jit` refuses a test kernel | it needs functions defined in a `.py` file, not in `python -c` | put the kernel in a file |

## Captions and triggers

| symptom | cause | action |
|---|---|---|
| a long trigger splits into many tokens | underscores, version suffixes and real-word fragments | verify the tokenization before captions are written; fragments of common words carry their priors |
| eval prompts lack the trigger | the eval prompt builder read its conventions from another place than the caption writer | maintainer: a prompt meant to match training captions comes from the same function as the captions; compare one assembled prompt with one training caption before a long render |

Count tokens before every run (CLIP-L, the SDXL tokenizer; prints min, median, max; above 75 needs
`--max_token_length 150`):

```
python -c "import glob; from transformers import CLIPTokenizer as T; t=T.from_pretrained('openai/clip-vit-large-patch14'); n=sorted(len(t(open(p, encoding='utf-8').read())['input_ids'])-2 for p in glob.glob('datasets/<version>/training/*.txt')); print(n[0], n[len(n)//2], n[-1])"
```

## Windows and shell

| symptom | cause | action |
|---|---|---|
| `TomlDecodeError: Found invalid character in key name: '['` at line 1 column 2 | a UTF-8 byte-order mark written by PowerShell 5.1 | write files without a BOM; read with `utf-8-sig` |
| `The string is missing the terminator` two lines away | a non-ASCII character in a `.ps1` file (read as ANSI) | keep `.ps1` files ASCII |
| the run dies before training with `RemoteException` | `$ErrorActionPreference = "Stop"` turns Python warnings on stderr into failures | `Stop` for your own cmdlets, `Continue` around native calls; check the exit code |
| `UnicodeEncodeError: 'charmap' codec` on `--help` or in the log | the help text is partly Japanese; the console code page cannot encode it | set `PYTHONIOENCODING=utf-8`; the install is fine |
| grep finds nothing in a log | the log was written as UTF-16LE by `Tee-Object` | read the trainer's own UTF-8 log file, not a console capture |
| grep prints megabytes of one line | a progress-bar log uses `\r`, not `\n` | `tail -c 2000 <run>.log \| tr '\r' '\n' \| tail -5` |
| a scripted edit breaks Python strings | a shell heredoc turned `\n` into a line break and `\b` into a backspace byte, or dropped doubled backslashes | write scripts with backslashes to a file and run it; afterwards check the file for `\x08` |
| a journal or note line ran part of itself as a command | an unquoted heredoc runs the backtick spans inside it | quote the heredoc delimiter (`<<'EOF'`), or pass the text as one quoted argument |
| a shell call hangs until its timeout | a command that reads stdin (for example `cat > file`) got no input | never put a stdin-reading command in a non-interactive call; write files with an editor tool or a script |
| `python -c` with several lines breaks on quoting | shell quoting | write a `.py` file and run that |
| a file rewritten by a Python one-liner now has CRLF line endings | `Path.write_text` on Windows translates `\n` to CRLF | write with `newline="\n"` or `write_bytes`; check line endings after any scripted rewrite |
| `import re` (or another stdlib import) fails in a helper script | the script ran with its working folder inside a package that has a module of the same name (`src/kiln/commands/types.py` shadows `types`) | run helper scripts from the repository root or a scratch folder, with `python -I` |
| grep in Git Bash aborts (core dumped) on a long listing | several `-e` patterns on a large input | one pattern per call, or a search tool |
| the same command behaves differently in two shell calls | an environment variable set in one call is gone in the next | set lasting values with `kiln config set`, not with an environment variable |

## Environment and eval renders

| symptom | cause | action |
|---|---|---|
| node packs break later, silently | the trainer was installed into ComfyUI's environment; its pins differ and node packs import those libraries | keep the trainer in its own venv (`train.venv_dir`) |
| `triton not found; flop counting will not work` | no triton wheel on Windows | harmless; training proceeds |
| the GPU memory monitor shows 0 after a run | the process has exited | read VRAM during the run for a peak |
| ComfyUI rejects the eval sampler with HTTP 400 | A1111 names (`euler_a`) instead of ComfyUI's (`euler_ancestral`) | map the names; print the server's message, not the bare status |
| eval renders hang instead of failing | a paused ComfyUI accepts the connection and never answers | the render client waits through a pause; a refused connection still fails; paused time does not count against the render timeout |
| eval renders on the wrong ComfyUI, or two servers answer | the training queue was started without the instance's ComfyUI settings, so its eval step fell back to the tool default `http://127.0.0.1:8188` | report it; operator: the queue must inherit the instance's `comfyui.url`, `comfyui.dir` and `comfyui.python`, and only the instance's server answers before a job is marked started |

## When something fails: the order

1. A smoke run (a few steps, a couple of minutes), submitted by the operator; kiln's `kiln train smoke` is
   planned for session 2. If it fails, nothing else is worth debugging.
2. Validate the dataset config with kohya's schema (above).
3. Read the log, not the console.
4. Read this file again before deriving a fix; most of these messages name the wrong file.

## Before you accept a LoRA: the fp8 quality check

`--fp8_base_unet` quantizes the base UNet's weights, which can soften the fine detail an identity LoRA
depends on. Before a checkpoint is called final, the operator compares the eval suite rendered with and
without fp8 on the hard-fail items of the project's checklist, not on overall prettiness. If quality suffers,
768 without fp8 is the fallback, but measure it first with a short measuring run; a figure quoted for it
earlier had been measured with fp8 on.
