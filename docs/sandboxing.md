# Sandboxing Technologies for AI Coding Agents

AI coding agents (such as Claude Code, OpenHands, Devin, E2B, and Modal) require sandboxing to safely execute untrusted, agent-generated code and arbitrary shell commands. 

In practice, sandboxing solutions divide into **two primary categories** based on deployment architecture:

1. **Cloud / Multi-Tenant Infrastructure** — where untrusted code from multiple users runs on shared compute resources.
2. **Local Developer Workstations** — where CLI agents run on a single developer's host machine with direct access to local files and toolchains.

Traditional OCI containers (Docker, Podman) sit between these two paradigms. While containers excel at application packaging and dependency reproducibility, they involve specific tradeoffs when used as agent execution environments.

---

## 1. Cloud & Multi-Tenant Environments: MicroVMs vs. Traditional Containers

When an agent executes code in a cloud platform (such as E2B, Modal, Fly Machines, or AWS Lambda), the primary challenge is **multi-tenant security** over a **shared Linux kernel**.

### The Shared Kernel Attack Surface

Standard OCI containers (Docker, Podman, `runc`, `crun`) are not full virtual machines. They isolate processes using Linux kernel primitives: **namespaces** (`pid`, `net`, `mnt`, `ipc`, `uts`, `user`) and **control groups (cgroups)**.

Because all containers on a host share the same underlying Linux kernel, untrusted code that exploits a kernel vulnerability (e.g., privilege escalation, Dirty COW, eBPF bugs, or race conditions) can break out of container boundaries (**container breakout**) and compromise the host or other tenants.

```mermaid
flowchart TB
    subgraph OCI["Traditional OCI Container (Shared Kernel)"]
        direction TB
        c1["Container A<br/>(Agent Code)"]
        c2["Container B<br/>(Other Tenant)"]
        k1["Shared Host Linux Kernel<br/>(Namespaces + Cgroups)"]
        hw1["Host Hardware"]
        c1 -.->|Attack Surface / Exploits| k1
        c2 --- k1
        k1 --- hw1
    end

    subgraph MicroVM["MicroVM Isolation (Dedicated Kernels)"]
        direction TB
        v1["MicroVM A<br/>Agent Code + Minimal Guest Kernel"]
        v2["MicroVM B<br/>Other Tenant + Minimal Guest Kernel"]
        hyp["Hardware Hypervisor (KVM / VT-x / AMD-V)"]
        hw2["Host Hardware"]
        v1 --- hyp
        v2 --- hyp
        hyp --- hw2
    end
```

### Hardware-Assisted MicroVM Technologies

To eliminate the shared-kernel risk, cloud sandboxing platforms rely on lightweight hardware virtualization and user-space kernels:

* **Firecracker & Kata Containers**:
  Use Linux KVM (Kernel-based Virtual Machine) hardware virtualization (`VT-x` / `AMD-V`). Every agent execution instance runs inside its own minimalist, stripped-down guest Linux kernel. Even if agent code achieves root privileges and fully compromises the guest kernel, it remains confined inside the virtual machine boundary.
* **gVisor (Google)**:
  Acts as an application kernel written in Go (`Sentry`). It intercepts and handles application syscalls in user space, presenting a virtualized kernel interface without giving the container direct access to the host kernel.
* **Fast Boot & Memory Snapshots**:
  Modern MicroVMs (like Firecracker) initialize in **5–50 milliseconds** and consume minimal memory overhead (~5 MB per VM). They support instant **memory snapshot restoration**, allowing cloud platforms to provide ephemeral, pre-warmed execution environments with container-like startup latency and true VM-grade isolation.

---

## 2. Local Developer Workstations: Host Kernel Sandboxing & Ergonomics

When running coding agents directly on a developer's machine as a CLI tool (e.g., local coding assistants and CLI agents), the trade-offs shift. Security remains important, but **developer ergonomics** and **local toolchain integration** become the primary constraints.

### Tradeoffs of Running Local Containers

Attempting to run every local agent action inside a standard container creates several friction points:

1. **UID/GID and File Permission Conflicts**:
   Containers running without explicit user mapping create files on host bind mounts as `root` (UID 0), preventing the developer from editing or deleting them on the host without `sudo` or `chown`.
2. **Missing Host Toolchains and Caches**:
   Developers maintain optimized compilers, language runtimes, package caches (`~/.npm`, `~/.cache`, `node_modules`, `target/`, `.venv`), Git credentials, and global development utilities on their host. Containers either require re-installing dependencies or configuring complex, fragile volume bind-mounts.
3. **VM and Daemon Latency**:
   On macOS and Windows, running containers requires a background Linux VM (e.g., Podman machine or Docker Desktop), adding CPU, memory, and filesystem I/O overhead.

### Host-Level Kernel Sandboxing

Local CLI agents increasingly use lightweight, in-process host-level sandboxing:

* **Linux**:
  * **`bubblewrap` (`bwrap`)**: Unprivileged sandboxing that creates ephemeral user, mount, and network namespaces without daemon overhead.
  * **`Landlock`**: An unprivileged Linux Security Module (LSM) allowing processes to restrict their own filesystem access at runtime.
  * **`seccomp-bpf`**: System call filtering to restrict dangerous operations.
* **macOS**:
  * **`Seatbelt` (`sandbox-exec` / `libsandbox`)**: Kernel-enforced sandboxing profiles that restrict process I/O and network operations.

### Benefits for Local Agent Workflows

* **Transparent Tool Access**: The agent directly invokes the developer's installed compilers, formatters, and linters with read-only access to system binaries.
* **Fine-Grained Path Restriction**: Sandboxing rules enforce write permissions strictly on the active project directory (`./workspace`, `.`) and `/tmp`, while blocking access to sensitive user directories (`~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.bashrc`).
* **Zero Startup Delay**: Sandboxes apply instantly at process launch without hypervisors, container daemon checks, or VM boot times.

---

## 3. Technology Comparison Matrix

| Layer | Core Technologies | Key Strengths | Tradeoffs in Agent Execution |
| :--- | :--- | :--- | :--- |
| **Host Kernel Sandboxing** | `bubblewrap`, `Landlock`, `seccomp`, macOS `sandbox-exec` | Near-zero startup latency, direct access to host toolchains and caches, fine-grained path restrictions. | Shared host kernel; tied to OS-specific security primitives; depends on host environment consistency. |
| **OCI Containers** | `podman`, `docker`, `runc`, `crun` | Strong environment reproducibility, hermetic dependency bundling, portable across hosts. | Shared host kernel (insufficient for untrusted cloud multi-tenancy); filesystem permission and bind-mount complexity locally. |
| **MicroVMs / Virtualization** | `Firecracker`, `Kata Containers`, `gVisor`, Apple `Virtualization.framework` | Hardware-enforced VM isolation boundary, dedicated guest kernel per execution, snapshot restore support. | Higher setup complexity locally; requires hypervisor/KVM hardware support; heavier resource footprint on local workstations. |

---

## 4. Why `pi-container` Uses Containers as Its Sandboxing Technology

While cloud systems trend toward MicroVMs and lightweight CLI agents trend toward host-level filtering, **`pi-container` uses container-based sandboxing specifically for two fundamental architectural advantages**:

1. **Ease of Orchestrating the Traffic-Intercepting Auditing Proxy**:
   Transparent network auditing and domain allowlisting require isolating the agent's network stack so that traffic cannot escape uninspected. OCI container networks provide this out-of-the-box:
   - The agent runs inside an isolated, gateway-less container network (`--internal`).
   - L3/L4 routing rules redirect all HTTP, HTTPS, and DNS traffic transparently through a companion `mitmproxy` proxy container.
   - Domain allowlists, secret redaction, and flow logging operate reliably without needing complex host-level packet filters, root-level firewall rules, or host OS-specific network hooks.

2. **Standardized and Reproducible Project Workspaces**:
   Rather than relying on whatever tools happen to be installed on the host machine, containerization provides a hermetic, predictable environment:
   - Packages an audited base toolchain (Node, Python, `uv`, rootless Podman, netavark) compiled from source.
   - Protects the developer's host machine from accidental global package installs, state drift, or configuration pollution.
   - Allows each project workspace to define its own persistent volume mounts and project-specific dependencies without impacting other workspaces or the host system.

---

## Summary

The architecture of AI coding sandboxes is defined by the trust model and operational goals:

* **Cloud multi-tenant platforms** require **MicroVMs** (Firecracker, Kata) for hardware-level kernel isolation against untrusted tenant workloads.
* **Local CLI coding assistants** often use **host-level kernel filtering** (Landlock, bubblewrap, Seatbelt) for lightweight, zero-latency execution against existing host tools.
* **Auditable, isolated development sandboxes** (like `pi-container`) use **rootless OCI containers and network proxying** because they provide the ideal foundation for **effortless proxy interception and traffic auditing** while delivering **standardized, reproducible workspaces**.

