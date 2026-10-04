# Goal-Replay

Every 2022 World Cup goal replayed in 3D from real tracking data.

## Rebuilding the clips

```sh
source .venv/bin/activate
python pipeline/build_all.py        # clips/ and clips/index.json from data/raw/
python pipeline/shot_placement.py   # pipeline/shot_placement.json, needs clips/index.json
python pipeline/build_all.py        # again, so every shot is aimed at its placement
python -m pytest
```

`pipeline/shot_placement.json` is checked in, so the second build is only needed
when goals are added or removed.

## Data and credits

- Tracking, event and roster data: the PFF FC (Gradient Sports) 2022 World Cup
  dataset. It is not included in this repository.
- Where each shot crossed the goal line: [StatsBomb Open Data](https://github.com/statsbomb/open-data),
  used under the StatsBomb open data licence. Data provided by StatsBomb.
