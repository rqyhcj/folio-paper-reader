# Privacy and security

Folio is designed for personal use on the same Windows computer. The launcher binds to `127.0.0.1`; it does not provide multi-user authentication or permission isolation. Do not expose its port to the public Internet or a shared network without designing those controls.

- A fresh installation contains no selected endpoint, model, or API key. Provider suggestions are applied only after a user chooses one.
- Windows DPAPI protects saved API keys for the current system user. Complete keys are not returned to the frontend, logged, or included in exported files. The frontend can display a newly typed key, but cannot retrieve a saved key.
- Changing provider or endpoint clears the stored credential unless the user explicitly supplies a new one. Local Ollama on a loopback address may be used without a key; remote endpoints require a key and HTTPS.
- Translation sends extracted paragraph text to the provider explicitly configured by the user. Imported PDFs and extracted PNG figures remain local; this version does not send figure pixels to the model.
- Text in a PDF is treated as material to translate, rather than instructions for operating the application. Translations may contain errors; original text remains available for checking.
- Reading settings are stored in browser local storage. PDFs, translations, figures, and encrypted credentials are stored under the local `data/` directory. Neither is submitted with the source repository.
- `data/`, generated outputs, `.env` files, private key files, databases, logs, virtual environments, and release packages are excluded from Git and source archives. Keep your own backups private.

If you find a security issue, avoid posting real API keys, private PDFs, decrypted settings, or confidential text in public issues. Use GitHub's private vulnerability reporting for this repository if available. Revoke or rotate a key if it has been exposed in a public place or an untrusted channel.
