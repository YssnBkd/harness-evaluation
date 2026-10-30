# inv

A tiny CSV-backed inventory.

```
python3 -m inv add bolts 100 0.05     # new item: name, qty, price
python3 -m inv add bolts 50           # restock
python3 -m inv list                   # table sorted by name, with the total value
python3 -m inv --db other.csv list    # another inventory file
```
