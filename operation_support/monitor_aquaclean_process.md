

```
└─$ tail -f /absolute/path/to/aquaclean_console_app/logs/aquaclean_$(date '+%Y.%m.%d').log
```

```
└─$ cat /proc/$(pgrep -f aquaclean_console_app)/statm  
29892 8284 3094 1535 0 7607 0
```

```
└─$ ps v $(pgrep -f aquaclean_console_app)                                          
    PID TTY      STAT   TIME  MAJFL   TRS   DRS   RSS %MEM COMMAND
   1405 ?        Sl    15:52      0     0 119568 33136  0.4 /usr/bin/python /absolute/path/to/aquaclean_console_app/main.py

```


See `/usr/src/kernel/Documentation/filesystems/proc.rst` (on Raspberry Pi):

##### Table 1-3: Contents of the statm files (as of 2.6.8-rc3) 

| Field    | Content                    |                            |
| --- | --- | ---|
| size     | total program size (pages) | (same as VmSize in status) |
| resident | size of memory portions  (pages)   | (same as VmRSS in status)  |
| shared   | number of pages that are shared | (i.e. backed by a file, same as RssFile+RssShmem in status) |                            |
| trs      | number of pages that are \'code\'                  | (not including libs; broken, includes data segment) |
| lrs      | number of pages of library | (always 0 on 2.6)          |
| drs      | number of pages of data/stack                        | (including libs; broken,includes library text)                         |
| dt       | number of dirty pages | (always 0 on 2.6)              |



```
┌──(kali㉿raspi-5)-[/usr/src/kernel/Documentation]
└─$ free -h                                            
               total        used        free      shared  buff/cache   available
Mem:           7.8Gi       236Mi       5.3Gi       6.7Mi       2.3Gi       7.5Gi
Swap:             0B          0B          0B                                              
```


`PYTHONMALLOC=debug PYTHONASYNCIODEBUG=1  PYTHONTRACEMALLOC=1 python -W default -X faulthandler /home/kali/homeautomation/geberit-py_bleak/V7_2024-12-03/toggleLidPosition.py`
