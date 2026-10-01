# Forager

Moves passivbots around to wherever volatility is highest.  

## How it works

The script does the following:
- fetch min costs for all symbols
- filter symbols by min cost
- get ohlcvs for all approved symbols
- filter symbols by volume, unilateralness, noisiness
- get the account's currently active bots
- generate and dump yaml file with new bots and old bots with graceful-stop
- sleep for 60 secs to avoid API rate limiting due to many calls to fetch ohlcvs
- kill previous tmux session
- use tmuxp to load new tmux session
- sleep for one hour
- repeat  

The script is also useful for starting a static set of bots, similar to the manager (see docs/manager.md).  
Add kwarg `-n/--noloop` to stop the forager script after one iteration.


## Usage
```shell
python3 forager.py configs/forager/config_file.hjson
```

## Requirements
In addition to requirements.txt, tmux and tmuxp are needed. Install with `apt install tmuxp`   

## Config

A template example config is found in configs/forager/example_config.hjson

| Parameter                     	| Description |
| ----------------------------------| ------------- |
| `user`							| User as defined in api-keys.json.
| `twe_long`						| Total Wallet Exposure Long.  WE_limit per bot is TWE / n_bots.
| `twe_short`						| Total Wallet Exposure Short.  WE_limit per bot is TWE / n_bots.
| `n_longs`							| Number of long bots.
| `n_shorts`						| Number of short bots.
| `max_min_cost`					| Exclude symbols whose min_cost > max_min_cost.
| `n_ohlcvs`						| Number of ohlcvs to fetch (100 candles of 15m is 25 hours).
| `ohlcv_interval`					| Default is 15m.
| `leverage`						| Set leverage.
| `price_distance_threshold`		| Don't make limit orders whose price is further away from market price than price_distance_threshold.
| `volume_clip_threshold`			| Include x% of the highest volume coins.
| `unilateralness_clip_threshold`	| Include x% of symbols with lowest unilateralness.
| `max_n_panes`						| Max number of panes per tmux window.
| `default_config_path`				| Default config to use.
| `symbols_to_ignore`				| Don't create bots for these symbols.
| `approved_symbols_long`			| If empty, allow all symbols for long.
| `approved_symbols_short`			| If empty, allow all symbols for short.
| `live_configs_dir_{long/short}`	| Search this directory for live configs whose filename contains symbol name. Leave as empty string to disable. If multiple matches, the first alphabetically sorted is selected.
| `live_configs_map`				| Live configs to to use with particular symbols.
| `live_configs_map_{long/short}`	| Overrides symbols from live_configs_map and live_configs_dir.
| `long_mode` / `short_mode`		| Optional. Mode for all long/short bots. See "Long/short modes" below.

Configs are chosen in the following priority:  
- `live_configs_map_{long/short}`,  
- `live_configs_map`
- `live_configs_dir_{long/short}`
- `default_config_path`


## Long/short modes (`long_mode`, `short_mode`)

Optional keys in the forager config. They are read at startup and applied on every
iteration, to every pane and to every symbol forager rotates in or out.

| Value (long or short code)                 | passivbot flag | Effect in forager |
| ------------------------------------------ | -------------- | ----------------- |
| absent, `null` or `""`                     | -              | Same behaviour as before (no change). |
| `normal` / `n`                             | -              | Same as absent: active bots `n`, rotated-out bots `gs`. |
| `graceful_stop` / `gs` / `graceful-stop`   | `gs`           | Same as `-gsl` / `-gss`: no new symbols on that side, current positions kept in graceful stop, WE_limit per bot unchanged. |
| `manual` / `m`                             | `m`            | No new symbols on that side; every pane gets `-lm m` / `-sm m`. |
| `panic` / `p`                              | `p`            | No new symbols on that side; every pane gets `-lm p` / `-sm p`. |
| `tp_only` / `t` / `tp-only`                | `t`            | No new symbols on that side; every pane gets `-lm t` / `-sm t`. |

Values are case-insensitive. Any other value makes forager exit at startup with an error,
because passivbot.py silently ignores unknown `-lm`/`-sm` values.

When a symbol runs long and short with different configs (two instances), the instance that
only handles the other side keeps `-lm m` / `-sm m` as before.

The CLI flags `-gs`, `-gsl` and `-gss` still work and are combined with these keys.

Example:
```hjson
long_mode: graceful_stop
short_mode: normal
```
