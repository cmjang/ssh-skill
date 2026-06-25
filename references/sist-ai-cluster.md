# ShanghaiTech SIST AI Cluster (上科大信息学院 AI Cluster)

Worked-example profile for the ShanghaiTech (上科大, **skd**) School of Information Science and
Technology AI Cluster. It ships as the built-in cluster profile `sist_ai_cluster` (aliases: `skd`,
`skd_ai_cluster`, `sist`, `shanghaitech`, `shanghaitech_sist`). Read this when operating that cluster
or when using it as a template for another jump-host Slurm cluster.

> The internal addresses below are campus-private (RFC1918) and only reachable from inside the
> ShanghaiTech network/VPN. For a different site, copy this profile to a private JSON file under
> `$SSH_SKILL_HOME/cluster_profiles/` instead of committing real hosts.

## Topology: you must jump through a login node

The defining property of this cluster: **the GPU debug nodes are only reachable by first connecting
to a login node.** The login nodes sit on `10.15.89.x`; the debug nodes sit on `10.15.88.x` and are
not directly routable from outside. `ssh-skill` models this with `ProxyJump`.

| Role | Hosts | Port | Reachable how | Slurm |
| --- | --- | --- | --- | --- |
| Login | `10.15.89.191`, `10.15.89.192`, `10.15.89.41` | `22112` | directly (inside campus net) | submit here |
| Debug (GPU) | `10.15.88.73`, `10.15.88.74` | `22112` | only via ProxyJump through a login node | **do not submit jobs here** |

Login and debug rules from the manual:

- Log in to the cluster only through the three login nodes on port `22112`.
- Do not run heavy workloads on login nodes; an admin may kill them.
- Do not hop from one login node to another; reconnect from your own client instead.
- Debug nodes are for code editing and environment debugging; you cannot submit jobs or otherwise
  reach the batch system from them.
- Close the SSH session when work is done.
- Prefer key-based login. Keep the private key tight (`chmod 600`, or `500` as the manual suggests).
- On a host-key conflict, remove the stale line from `~/.ssh/known_hosts` and reconnect.
- Change your password with `yppasswd`.

## One-command setup

Provision all five hosts (3 login + 2 debug) with the correct ProxyJump and roles in one step:

```bash
python3 $SSH_SKILL_DIR/scripts/ssh_registry.py provision-cluster skd \
    --user <your_username> --identity-file ~/.ssh/shanghaitech_hpc_key
```

This creates managed aliases `sist-login1..3` (direct submit hosts) and `sist-debug1..2`
(role=debug, `ProxyJump sist-login1`), all bound to `cluster_profile=sist_ai_cluster`. Preview first
with `--dry-run`; choose a different jump node with `--login-jump-index 1`; rename the aliases with
`--prefix myskd`.

After that, `ssh sist-debug1` (and every `ssh-skill` tool with `host="sist-debug1"`) transparently
hops through `sist-login1` — no manual two-hop needed.

### Equivalent plain `~/.ssh/config`

If you prefer not to use the registry, the same topology in standard SSH config is:

```sshconfig
Host sist-login1
    HostName 10.15.89.191
    User <your_username>
    Port 22112
    IdentityFile ~/.ssh/shanghaitech_hpc_key

Host sist-debug1
    HostName 10.15.88.73
    User <your_username>
    Port 22112
    IdentityFile ~/.ssh/shanghaitech_hpc_key
    ProxyJump sist-login1
```

(Repeat for `sist-login2/3` and `sist-debug2`.) Then `import-alias` each one if you want them in the
registry with the cluster profile attached.

## Software policy

Users hold unprivileged accounts. **No `sudo`, no `apt`, no system-level installs.** Install into
your home directory; almost everything offers a source build. Ask admins only for genuinely hard
installs.

- Base toolchain on every node: `make 4.3.0`, `gcc 10.2.0`, `glibc 2.31`.
- Software lives under `/public/software`; installers/source under `/public/resources/depository`.
- Use environment modules: `module avail`, then `module load <name>/<version>`.

| Module | Versions |
| --- | --- |
| `anaconda3` | 4.10.3 |
| `cmake` | 3.22.0 |
| `gcc` | 10.2.0, 7.5 |
| `automake` | 1.15, 1.16.5 |
| `cuda` | 8.0–12.5 |
| `make` | 4.3 |
| `singularity` | 3.5.2 |

AI users should maintain their own environment with `conda` or a self-built Python (the anaconda
installer is also under `/public/resources/depository`).

### Faster downloads (mirror)

Use the GeekPie/ShanghaiTech mirror for pip and conda:

- pip: <https://mirrors.shanghaitech.edu.cn/help/pypi>
- conda: <https://mirrors.shanghaitech.edu.cn/help/anaconda>

### Containers: Singularity, not Docker

Docker is not supported. Use **Singularity 3.5.2**, which can run Docker-format images. There is no
`sudo` inside containers either, so build a fully prepared image *before* uploading:

- Converting a local Docker image exported to `.tar` into a Singularity image **on the cluster**
  needs no admin rights — this is the recommended path.
- Building from a `Dockerfile` needs root, which normal users lack: use Singularity `--remote` build
  (register on the Singularity site) or contact admins.

## Running jobs (Slurm)

- Submit from a login node (`sist-login1/2/3`). `ssh-skill` refuses `ssh_sbatch_submit` /
  `ssh_scancel` against a `role=debug` host.
- Partition names, GPU types, accounts, and time limits are site-specific — **discover them live**
  with `ssh_sinfo(host="sist-login1")` before requesting resources. Do not hard-code partitions.
- Declare GPUs explicitly, e.g. `--gres=gpu:1` or `--gres=gpu:<type>:<n>`; confirm the GPU-type
  token via `sinfo`.
- Typical flow: `ssh_sync_dir` → (`ssh_list_conda_envs` or `module load`) → `ssh_sinfo` →
  `ssh_render_slurm_script` → `ssh_sbatch_submit` → `ssh_squeue` → `ssh_tail_file` → `ssh_sacct`.

## Mapping to ssh-skill tools

- Edit/debug code on GPU: target `sist-debug1` (auto-jumps through a login node).
- Submit/monitor training: target a login alias `sist-login1`.
- `ssh_get_cluster_profile(host="sist-login1")` or `ssh_get_cluster_profile(profile_id="skd")`
  surfaces these rules, the module list, and the mirror URLs in context.
