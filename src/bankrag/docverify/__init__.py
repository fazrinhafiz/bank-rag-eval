"""Document intake and tamper detection for loan applications (payslips and bank statements).

    PDF -> text layer + fonts + metadata -> fields -> consistency checks, PDF forensics, anomaly score,
    duplicate registry -> YAML rule engine -> auto_accept | manual_review | reject, with reasons.

Needs the `docs` extra (pypdf) to verify documents, and `synth` (fpdf2) to generate the synthetic corpus:
    pip install -e ".[docs,synth]"   - the API image installs `docs` only.
"""
