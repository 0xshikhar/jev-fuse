# Security Policy

## 🛡️ Supported Versions

We provide security updates and patches for the following versions:

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |
| < 0.1.0 | :x:                |

---

## 🚨 Reporting a Vulnerability

As an open governance runtime and safety gate for autonomous coding agents, the security and reliability of JEV Fuse is our top priority.

If you believe you have discovered:
- A bypass or evasion of the 4-tier shell guard (`fuse_guard` / `PreToolUse` hook),
- An unintended elevation of privilege or command execution vector,
- An unhandled data leak or secret exposure vulnerability, or
- A denial-of-service / memory exhaustion bug in the proxy or cache layers,

**PLEASE DO NOT report security vulnerabilities through public GitHub issues.**

Instead, please report security concerns privately using one of the following methods:
1. **GitHub Private Vulnerability Reporting**: Submit a report directly under the repository's **Security** tab -> **Report a vulnerability**.
2. **Email**: Send detailed information to the maintainers at `security@jevfuse.org` or `shikhar@0xshikhar.com`.

### What to Include in Your Report
To help us triage and resolve the issue quickly, please include:
- A clear description of the vulnerability and the affected component (e.g., shell parser, singleflight coalescer, REST gateway).
- Minimal reproduction steps, including sample input commands, transcript JSON, or cURL requests.
- Potential impact and threat model (e.g., how an autonomous agent or malicious payload could trigger the bypass).
- Any proposed mitigations or fixes if you have them.

---

## ⏱️ Response & Disclosure Timeline

- **Initial Acknowledgment**: Within 48 hours of receipt.
- **Triage & Assessment**: Within 5 business days.
- **Patch Release & Advisory**: We coordinate coordinated disclosure and release patches promptly with credit to the reporter in our release notes.
