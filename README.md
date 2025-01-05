# geberit-aquaclean
Python library to connect to the [Geberit AquaClean](https://www.geberit.de/badezimmerprodukte/wcs-urinale/dusch-wcs-geberit-aquaclean/produkte/ "Geberit AquaClean") Mera toilet ( port from Thomas Bingels C#  library)

This is a port of [Thomas Bingel](https://github.com/thomas-bingel "Thomas Bingel")'s great [geberit-aquaclean project](https://github.com/thomas-bingel/geberit-aquaclean "geberit-aquaclean project") from C# to Python.
It connects to the Geberit AquaClean Mera toilet and works fine for basic controlling of the toilet.

## Use cases

- Connect [Geberit AquaClean](https://www.geberit.de/badezimmerprodukte/wcs-urinale/dusch-wcs-geberit-aquaclean/produkte/ "Geberit AquaClean") to the home automation software of your choice via [MQTT](http://mqtt.org/ "MQTT") broker, e.g. [openHAB](https://www.openhab.org "openHAB") (see [Geberit AquaClean with openHAB - Basic UI](./operation_support/Geberit%20AquaClean%20with%20openHAB%20-%20Basic%20UI.png)) or [Home Assistant](https://www.home-assistant.io "Home Assistant").

- Control [Geberit AquaClean](https://www.geberit.de/badezimmerprodukte/wcs-urinale/dusch-wcs-geberit-aquaclean/produkte/ "Geberit AquaClean") via e.g. Apple Homekit and your home automation software by voice. (Voice control Geberit AquaClean)

- Greet the user appropriately as they take their seat. ("*Schön, dass Du Platz genommen hast.*")

- Play music to minimise noise during a session. (Active Noise Cancellation, ANC for [Geberit AquaClean](https://www.geberit.de/badezimmerprodukte/wcs-urinale/dusch-wcs-geberit-aquaclean/produkte/ "Geberit AquaClean")). E.g. *Die schöne Müllerin, Op. 25, D. 795: Wohin? - Ich hört ein Bächlein rauschen · Fritz Wunderlich · Franz Schubert*

- Dismiss the user when they leave the toilet. ("*Prima, dass wir ins Geschäft gekommen waren.*")

- Inform the user of the duration of their session. ("*Wir hatten für 3 Minuten und 19 Sekunden das Vergnügen.*")




## Personal motivation

I have been involved in smart home since 1999.
The term didn't exist at that time.

Now the task was to integrate a [Geberit AquaClean](https://www.geberit.de/badezimmerprodukte/wcs-urinale/dusch-wcs-geberit-aquaclean/produkte/ "Geberit AquaClean") Mera Comfort into the SmartHome ecosystem.
As the appliance comes with a remote control and an app, there had to be interfaces.
During my research I came across [Thomas Bingel](https://github.com/thomas-bingel "Thomas Bingel")'s ['Geberit AquaClean Mera Library'](https://github.com/thomas-bingel/geberit-aquaclean "'Geberit AquaClean Mera Library'") project.

I managed to compile my very first C# project.
After 
 - adapting the addresses (BLE address of the AquaClean, address of the mqtt server) and 
 - allowing access to the private network ( see [Universal Windows project - HttpClient exception](https://stackoverflow.com/questions/33235131/universal-windows-project-httpclient-exception "Universal Windows project - HttpClient exception"): `Double-click the Package.appxmanifest file in your project. Click on the Capabilities tab. Add the Private Networks capability to your project.`)

Success right away.
Much respect and thanks to [Thomas Bingel](https://github.com/thomas-bingel "Thomas Bingel")!

However, I wanted to run the application headless as a service in the background on a Raspberry.

After further research, a Python application seemed the way to go - even though I had never written a Python application before.

The beginning was very tedious until I discovered Copilot as a supporter.
In the end, Copilot took over the porting of [Thomas Bingel](https://github.com/thomas-bingel "Thomas Bingel")'s C# application to Python.

I added a configuration option via file to the application.

In my case, the application runs smoothly on a Raspberry 5 (which I happened to have at hand) running Kali Linux 2024.4 (Ubuntu and Debian should also work).
The AquaClean has firmware version: RS28.0 TS199.


The application shows status changes in MQTT Explorer for

- userIsSitting
- analShowerIsRunnin
- ladyShowerIsRunning
- dryerIsRunning

I can control the AquaClean lid using `publish toggleLidPosition`.

My main goal was to use the proximity sensor to control other lights (in addition to the AquaClean's orientation light).
Unfortunately this did not work. `OrientationLightState` always stays at `0`, which was also the case in [Thomas Bingel](https://github.com/thomas-bingel "Thomas Bingel")'s application.

## Tested runtime enviroment

### AquaClean toilet
AquaClean firmware version: RS28.0 TS199

### Central device (machine the script is running on)

#### MacBookAir6,2, Dual-Core Intel Core i5
with

```
  Virtualization: oracle
Operating System: Ubuntu 24.04.1 LTS              
          Kernel: Linux 6.8.0-51-generic
    Architecture: x86-64
  Hardware Model: VirtualBox
````
and

SABRENT USB Bluetooth 4.0 Mikro Adapter
and Python 3.12.3

#### Raspberry Pi 5 Model B Rev 1.0

with
```
Operating System: Kali GNU/Linux Rolling
         Version: 2024.4   
          Kernel: Linux 6.1.64-v8+
    Architecture: arm64
```
and Python 3.12.8




## Installation

For now, just download and unpack the folder `aquaclean_console_app`.
Not very pythonic, but it fits the whole project ;)

### Dependencies



| Package                                                                   | Version   | Purpose                   |
| ------------------------------------------------------------------------- |-----------| --------------------------|
| [bleak](https://github.com/hbldh/bleak "bleak")                           | 0.22.3    | connect to BLE devices |
| [paho-mqtt](https://github.com/eclipse-paho/paho.mqtt.python "paho-mqtt") | 2.0.0     | connect to a [MQTT](http://mqtt.org/ "MQTT") broker |
| [aiorun](https://github.com/cjrh/aiorun "aiorun")                         | v2024.8.1 | handle shutdown sequence |
| [haggis](https://gitlab.com/madphysicist/haggis "haggis")                 | 0.14.1    | extend the logging framework |




## Usage
### check for Bluetooth Low Energy (BLE) connectivity

On Linux the bleak package (for handling the BLE stuff) is based on the `BlueZ DBUS API`.
It is recommended to check the connectivity to the Geberit AquaClen toilet on OS level first by using the same technology stack.

#### On Linux

Check whether the Bluetooth service is up:


`systemctl status bluetooth`

Output should be similar to 
```
● bluetooth.service - Bluetooth service
     Loaded: loaded (/usr/lib/systemd/system/bluetooth.service; enabled; preset: disabled)
     Active: active (running) since Thu 2024-12-19 10:12:55 CET; 1 day 8h ago
 Invocation: a19fb71d3ac54c55966ae8c3001c2611
       Docs: man:bluetoothd(8)
   Main PID: 494 (bluetoothd)
     Status: "Running"
      Tasks: 1 (limit: 9453)
        CPU: 50.093s
     CGroup: /system.slice/bluetooth.service
             └─494 /usr/libexec/bluetooth/bluetoothd

Dec 19 10:12:55 raspi-5 bluetoothd[494]: src/plugin.c:init_plugin() System does not support mcp plugin
Dec 19 10:12:55 raspi-5 bluetoothd[494]: src/plugin.c:init_plugin() System does not support vcp plugin
Dec 19 10:12:55 raspi-5 bluetoothd[494]: profiles/audio/micp.c:micp_init() D-Bus experimental not enabled
Dec 19 10:12:55 raspi-5 bluetoothd[494]: src/plugin.c:init_plugin() System does not support micp plugin
Dec 19 10:12:55 raspi-5 bluetoothd[494]: src/plugin.c:init_plugin() System does not support ccp plugin
Dec 19 10:12:55 raspi-5 bluetoothd[494]: src/plugin.c:init_plugin() System does not support csip plugin
Dec 19 10:12:55 raspi-5 bluetoothd[494]: src/plugin.c:init_plugin() System does not support asha plugin
Dec 19 10:12:55 raspi-5 bluetoothd[494]: Bluetooth management interface 1.22 initialized
Dec 19 10:12:55 raspi-5 bluetoothd[494]: Battery Provider Manager created
Dec 19 10:25:04 raspi-5 bluetoothd[494]: Battery Provider Manager created
```

Check whether the Geberit AquaClean toilet is visible using the  *Bluetooth Control Command Line Tool* `bluetoothctl`

Once `bluetoothctl` is started use the command `scan on` to start the discovery. Use `scan off` to stop the discovery and `quit` to leave `bluetoothctl`

The discovery includes `[bluetooth]# [NEW] Device XX:XX:XX:XX:XX:XX Geberit AC PRO` in case of success:

```
└─$ bluetoothctl              
[bluetooth]# hci0 new_settings: powered bondable ssp br/edr le secure-conn 
[bluetooth]# hci1 new_settings: powered bondable ssp br/edr le secure-conn 
[bluetooth]# Agent registered
[bluetooth]# [CHG] Controller D8:3A:DD:D6:52:8F Pairable: yes
[bluetooth]# [CHG] Controller 00:1A:7D:DA:71:13 Pairable: yes
[bluetooth]# scan on
[bluetooth]# SetDiscoveryFilter success
[bluetooth]# Discovery started
[bluetooth]# [CHG] Controller 00:1A:7D:DA:71:13 Discovering: yes
[bluetooth]# [NEW] Device 7D:E1:81:42:4B:11 7D-E1-81-42-4B-11
[bluetooth]# [NEW] Device 65:4B:75:36:91:25 65-4B-75-36-91-25
[bluetooth]# [NEW] Device 52:C1:4E:1D:C0:35 52-C1-4E-1D-C0-35
[bluetooth]# [NEW] Device 58:3F:40:8F:C7:74 58-3F-40-8F-C7-74
[bluetooth]# [NEW] Device 38:AB:41:2A:0D:67 Geberit AC PRO
[bluetooth]# [NEW] Device 5B:9F:B4:E4:89:77 5B-9F-B4-E4-89-77
[bluetooth]# [NEW] Device 43:B4:93:D6:4F:5E 43-B4-93-D6-4F-5E
[bluetooth]# scan off[CHG] Device 5B:9F:B4:E4:89:77 RSSI: 0xffffffad (-83)
[bluetooth]# scan off
[bluetooth]# Discovery stopped
[bluetooth]# quit
```


To gain further knowledge, see the command  in  and he modules are useful .

For more information, see the `connect` command in `bluetoothctl` or use the [discover.py](https://github.com/hbldh/bleak/blob/develop/examples/discover.py "discover.py") and [service_explorer.py](https://github.com/hbldh/bleak/blob/develop/examples/service_explorer.py "service_explorer.py") modules from [bleak](https://github.com/hbldh/bleak "bleak").





### Configuration

#### BLE address

The device address found above must be entered as `device_id` in the `BLE` section of the `config.ini` file:

```
[BLE]
device_id = XX:XX:XX:XX:XX:XX
```

The *nRF Connect for Mobile* app developed by Nordic Semiconductor ASA can also help you find the BLE address.

#### MQTT Server address

Modify at least the `server` address in the `MQTT` section of the `config.ini` file as appropriate.

Edit the config file.
At least the address for the AquaClean and the MQTT server.

I found out the address of the AquaClean using the 'nRF Connect' app.

### run the console application

Just run `python /path/to/aquaclean_console_app/main.py` and watch the result in your favorite MQTT Tool.

In log_level `DEBUG`, the output is very similar to [Thomas Bingel](https://github.com/thomas-bingel "Thomas Bingel")'s ['Geberit AquaClean Mera Library'](https://github.com/thomas-bingel/geberit-aquaclean "'Geberit AquaClean Mera Library'").

#### Toggle lid position

Publish any value (payload is not evaluated) on the `Geberit/AquaClean/peripheralDevice/control/toggleLidPosition` topic to change the lid position using MQTT.



## Troubleshooting:

Two types of errors can occur:

1. **Cannot connect to the AquaClean**

   If the AquaClean receives an 'unintelligible' request, it can happen that the BLE connection is broken and the application terminates.
   When the application is restarted, it hangs with an exception similar to

   ```
   ...
   exception bleak.exc.BleakError: AquaClean device with address 38:AB:41:2A:0D:xx not found.

   __main__ YY ERROR: Check address or restart peripheral device (Geberit AquaClean) and wait a little while.
   ...
   ```

   **Solution**

   If the BLE address is correct, disconnect the AquaClean from the power supply for a few seconds and then reconnected it (i.e. forced a *restart*).

   Otherwise, correct BLE address. 


2. `retry due to le-connection-abort-by-local` **Output similar to**
    ```
    # ...
    bleak.backends.bluezdbus.client 211 DEBUG: Connectingto BlueZ path /org/bluez/hci0/dev_38_AB_41_2A_0D_67
    bleak.backends.bluezdbus.manager 872 DEBUG: receivedD-Bus signal: org.freedesktop.DBus.PropertiesPropertiesChanged (/org/bluez/hci0/dev_38_AB_41_2A_0D_67: ['org.bluez.Device1', {'Connected': <dbus_fastsignature.Variant ('b', True)>}, []]
    bleak.backends.bluezdbus.client 235 DEBUG: retry due to le-connection-abort-by-local
    ...
    __main__ YY ERROR: TimeoutError:
    ...
    ```

   **Solution**

   This error is not application related. It occurs when the connection cannot be established at Bluetooth level. This is recorded with bleak and also occurs independently of bleak. 

   Make sure there are no other Bluetooth or Wifi devices near the toilet and the computer running the script when you start the application.

   Restart the computer (and preferably the toilet) and wait a minute and 
   This should fix the problem.

   Run the script again.

   See [client.py @ bleak](https://github.com/hbldh/bleak/blob/e01e2640994b99066552b6161f84799712c396fa/bleak/backends/bluezdbus/client.py#L226 "client.py @ bleak"):


   ```
   # This error is often caused by RF interference
   # from other Bluetooth or Wi-Fi devices. In many
   # cases, retrying will connect successfully.
   # Note: this error was added in BlueZ 6.62.
   ```


## MQTT topics

The root node is configured in `config.ini`.

Default: `topic = Geberit/AquaClean`

The following topics are used:

- `Geberit/AquaClean/centralDevice/error                                       `
- `Geberit/AquaClean/centralDevice/connected                                   `
- `Geberit/AquaClean/peripheralDevice/information/Identification/SapNumber     `
- `Geberit/AquaClean/peripheralDevice/information/Identification/SerialNumber  `  
- `Geberit/AquaClean/peripheralDevice/information/Identification/ProductionDate`    
- `Geberit/AquaClean/peripheralDevice/information/Identification/Description   ` 
- `Geberit/AquaClean/peripheralDevice/information/initialOperationDate         `
- `Geberit/AquaClean/peripheralDevice/monitor/isUserSitting                    `
- `Geberit/AquaClean/peripheralDevice/monitor/isAnalShowerRunning              `
- `Geberit/AquaClean/peripheralDevice/monitor/isLadyShowerRunning              `
- `Geberit/AquaClean/peripheralDevice/monitor/isDryerRunning                   `
- `Geberit/AquaClean/peripheralDevice/control/toggleLidPosition                `

See [MQTT Explorer.png](https://github.com/jens62/geberit-aquaclean/blob/main/operation_support/MQTT%20Explorer.png)


## How does ist work?

If you are interested in the process, you can set the 

`log_level = TRACE` in `config.ini` and take a closer look to the output of

`
   python /path/to/aquaclean_console_app/main.py | grep ' called from '`
.

### A rough overview

A connection is established from the Python script (central device) to the AquaClean toilet (peripheral device) via Bluetooth Low Energy (BLE).
If the connection is successful, the AquaClean toilet sends a few (10?) so-called `InfoFrames`, which are ignored.
Subsequently *requests* are assembled from the Python script (see `AquaCleanClient.py`) and sent to the AquaClean toilet.
The *response* comes asynchronously and can consist of several chunks, so-called *frames*.
These response frames are collected (see `FrameCollector.py`), assembled into one transaction-response, deserialised/decoded and then published via mqtt.

The request to query the device status (`get_system_parameter_list_async`, e.g. `userIsSitting`, ...) is repeated every 2.5 seconds.
The request/response cycle for this is approximately 600 ms.

Set `log_level = TRACE` in `config.ini` and run

`python /path/to/aquaclean_console_app/main.py | grep 'getting the device changes took '`

to get the elapsed time for the request/response cycles.


The application has subscribed to the `Geberit/AquaClean/peripheralDevice/control/toggleLidPosition` topic via mqtt and triggers a corresponding request to the AquaClean toilet on according message.

## TODO

- brush up main.py
  - Double check shutdown process
  - Double check exception handling
  - Consolidate function to publish to mqtt
  - Replace `sleep` to wait for the `mqtt-client` to initialise with a `event_wait_queue.get(timeout=0.1)`
  - Make `orientationLightState` work
- Following [PEP 8 – Style Guide for Python Code](https://peps.python.org/pep-0008/ "PEP 8 – Style Guide for Python Code")'s guidelines, revise the programme.




## Ressources

[Bluetooth Low Energy in JavaScript und Node.js](https://entwickler.de/iot/wir-mussen-reden-002 "Bluetooth Low Energy in JavaScript und Node.js")


