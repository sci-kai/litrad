# Security

litrad runs on your own machine and stores your reading data locally.
When it serves on a network, it requires a password (salted scrypt hash,
signed session cookie) and rejects requests from other websites. It speaks
plain HTTP: use it in your own network or through a VPN, never expose the
port to the internet.

If you find a security problem (for example a way to read or change data
without the password), please do **not** open a public issue. Use GitHub's
*Report a vulnerability* (Security tab of the repository) instead, with the
steps to reproduce. You will get an answer within a few days.

Only the latest release is supported.
