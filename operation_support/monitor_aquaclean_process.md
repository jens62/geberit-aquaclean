
# Monitor `aquaclean-bridge`

## Service status

```bash
sudo systemctl status aquaclean-bridge
```

## Tail the log

```bash
tail -f /var/log/aquaclean/aquaclean.log
```

## Get process info

```bash
ps v $(pgrep -f aquaclean-bridge)
```

Example output:
```
   PID TTY      STAT   TIME  MAJFL   TRS   DRS   RSS %MEM COMMAND
  1405 ?        Sl    15:52      0     0 119568 33136  0.4 /home/kali/venv/bin/python /home/kali/venv/bin/aquaclean-bridge --mode api
```

## Monitor memory usage

```bash
free -h
```

```
               total        used        free      shared  buff/cache   available
Mem:           7.8Gi       236Mi       5.3Gi       6.7Mi       2.3Gi       7.5Gi
Swap:             0B          0B          0B
```

```bash
cat /proc/$(pgrep -f aquaclean-bridge)/statm
```

See `/usr/src/kernel/Documentation/filesystems/proc.rst` (on Raspberry Pi):

##### Table 1-3: Contents of the statm files (as of 2.6.8-rc3)

| Field    | Content                                                            |
|----------|--------------------------------------------------------------------|
| size     | total program size (pages) — same as VmSize in status             |
| resident | size of memory portions (pages) — same as VmRSS in status         |
| shared   | number of pages that are shared (backed by a file)                 |
| trs      | number of pages that are 'code' (not including libs)               |
| lrs      | number of pages of library (always 0 on 2.6)                       |
| drs      | number of pages of data/stack (including libs)                     |
| dt       | number of dirty pages (always 0 on 2.6)                            |

## Service management

```bash
sudo systemctl restart aquaclean-bridge
sudo systemctl stop    aquaclean-bridge
sudo systemctl start   aquaclean-bridge
```

## Run during development (foreground, debug flags)

```bash
PYTHONMALLOC=debug PYTHONASYNCIODEBUG=1 PYTHONTRACEMALLOC=1 \
  python -W default -X faulthandler \
  ~/venv/bin/aquaclean-bridge --mode api
```
