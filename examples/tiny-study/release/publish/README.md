# tiny-study

A synthetic threshold classifier. With seed 7 the drawn threshold 0.4648 gives
an accuracy of 0.8125 on 16 samples (`results/metrics.json`).

Run with `TINY_STUDY_ROOT` pointing to the folder that holds `data/`.

Checks, from this folder:

    sha256sum -c SHA256SUMS
    python3 verify_code.py
    python3 verify_payload.py
