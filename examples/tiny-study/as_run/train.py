#!/srv/fakelab/venv/bin/python
# -*- coding: utf-8 -*-
"""Tiny synthetic study: a threshold classifier on sixteen samples.

This whole tree is deliberately "dirty": it is the input of the example, as it would sit on a
lab machine. Each TRAP comment names what asrun removes or replaces. Nothing here is real.
"""
import csv
import json
import os
import random

# TRAP: an absolute root hard-coded on the lab machine. It becomes INTERNAL_DATA_ROOT.
DATA = "/srv/fakelab/tiny-study/data/samples.csv"
# TRAP: a folder name that must not be published. It becomes 'internal'.
team_notes_dir = "team_notes/round1"  # TRAP: identifier renamed to INTERNAL_NOTES_DIR
SOURCE = "Acme Field Survey"  # TRAP: a local value, replaced by ${SOURCE_NAME}
SEED = 7  # confidential: lab machine layout (TRAP: forbidden term, removed with the comment)


def load(path):
    """Read (x, y) pairs from a CSV file with columns x and y."""
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return [(float(r["x"]), int(r["y"])) for r in rows]


def main(name="metrics"):
    random.seed(SEED)
    # comentário em português: o limiar é sorteado com semente fixa (TRAP: non-English comment)
    data = load(os.environ.get("DATA_OVERRIDE", DATA))
    threshold = round(random.uniform(0.4, 0.6), 4)
    print("Carregando as amostras do levantamento de campo")
    hits = sum((x >= threshold) == bool(y) for x, y in data)
    acc = hits / len(data)
    print(f"Acurácia final de {acc:.4f} com {len(data)} amostras")
    out = os.environ.get("OUT_OVERRIDE", f"/srv/fakelab/tiny-study/results/{name}.json")
    record = {
        "accuracy": acc,
        "n_samples": len(data),
        "seed": SEED,
        "threshold": threshold,
        "data": DATA,
        "source": SOURCE,
        "notes_dir": team_notes_dir,
        "nota": f"Resultado obtido com semente {SEED} em {len(data)} amostras",
    }
    with open(out, "w") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
        f.write("\n")


if __name__ == "__main__":
    main()
