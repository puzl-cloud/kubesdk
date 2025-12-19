# Security Policy

## Supported Versions

We provide security fixes only for actively maintained branches.

| Version                          | Supported |
| -------------------------------- | --------- |
| Latest released minor (current)  | ✅        |
| Previous minor (current major)   | ✅        |
| Anything older                   | ❌        |

If you're unsure, report the issue anyway.

## Reporting a Vulnerability

### How to report

Use **[Security](https://github.com/puzl-cloud/kubesdk/security)** tab -> **Report a vulnerability** (private advisory).

Please include:
- Affected versions
- What the vulnerability is and its impact
- Minimal repro or PoC
- Any known mitigations

### What to expect

- Acknowledgement: within 3 business days.
- Updates while investigating: at least every 5 business days.

> We prioritize issues that allow RCE, privilege escalation, credential leakage, cross-namespace access, or supply-chain compromise.

## Disclosure

Please **do not disclose publicly** (issues, PRs, blog posts, social media, etc.) until:
- a fix is released, **or**
- we've agreed a coordinated disclosure date.

After releasing a fix, we'll publish an advisory with impact, fixed versions, and mitigation steps.
