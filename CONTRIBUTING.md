# Contributing to Procurement Receipt OCR

Thank you for your interest in contributing to **Procurement Receipt OCR & Automation Hub**! 🎉

We welcome contributions of all kinds: bug reports, documentation improvements, new business rules, UI enhancements, or additional OCR engine providers.

---

## 🛠️ Development Setup

1. **Fork and clone the repository:**
   ```bash
   git clone https://github.com/lynhattienksst-ops/procurement-receipt-ocr.git
   cd procurement-receipt-ocr
   ```

2. **Set up the environment:**
   ```bash
   cp .env.example .env
   # Add your Google Gemini API Key and GCP Service Account
   ```

3. **Start local containers:**
   ```bash
   docker compose up -d --build
   ```

4. **Run Unit Tests:**
   ```bash
   docker exec procurement-server python test_suite.py
   ```

---

## 📋 Guidelines

- **Code Style:** Follow PEP 8 for Python code.
- **Rules Integrity:** When modifying classification or row formatting rules in `services/business_rules.py`, always ensure all 5 core tests in `test_suite.py` pass.
- **Safety:** Never commit any live API keys, tokens, or Google Cloud Service Account JSON credentials.
- **Pull Requests:** Describe the rationale behind the change, steps to test, and attach screenshots for UI modifications.

---

## 💬 Community & Support

- Report bugs or request features via [GitHub Issues](https://github.com/lynhattienksst-ops/procurement-receipt-ocr/issues).
- Submit Pull Requests against the `main` branch.
