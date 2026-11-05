# GPI Touchdown

Turn eight dry contacts on an original Raspberry Pi Zero W into source selections for one shared video output. A closure selects a source; opening the contact leaves it selected.

| Device | Protocol | Default port |
| --- | --- | --- |
| Ross Ultrix | Pro-Bel SW-P-08 | TCP 8910 |
| Blackmagic Videohub | Videohub Ethernet | TCP 9990 |
| Blackmagic ATEM | Aux routing | UDP 9910 |

## Install

Start with Raspberry Pi OS Lite Bookworm **32-bit**. Connect the Pi to a network with internet access for installation, and copy this repository onto it.

```sh
sudo TOUCHDOWN_COUNTRY=US bash install.sh
sudo reboot
```

Use your country's two-letter Wi-Fi code. The installer prompts for an SSH password for `admin` and generates separate web and setup hotspot passwords. Find the latter two in the install output or in `touchdown-credentials.txt` on the boot partition.

On a network the Pi already knows, open [gpi-touchdown.local](http://gpi-touchdown.local/). If it cannot join saved Wi-Fi, connect to the **GPI-Touchdown** hotspot and open **http://192.168.50.1**. Sign in to the web page as `admin` and use the generated web password. Use the Wi-Fi form to join a 2.4 GHz network that can reach the router.

## Configure routing

1. Choose the device type and enter the device IP.
2. Set the shared output or aux and assign a source to each contact.
3. Enable routing, save, and try the **Route** button on a spare output.

Router inputs, outputs and aux numbers start at 1. In ATEM mode, save the switcher IP to load the available sources. ATEM source IDs are used as reported; 0 is Black. The ATEM client changes only the aux and checks its readback. Ultrix and Videohub report protocol acknowledgments.

## Wire the contacts

Use voltage-free contacts only. Connect each contact between its GPIO and ground. Do not apply external voltage to the GPIO.

| GPI | BCM GPIO | Physical pin |
| --- | --- | --- |
| 1 | 5 | 29 |
| 2 | 6 | 31 |
| 3 | 13 | 33 |
| 4 | 19 | 35 |
| 5 | 26 | 37 |
| 6 | 16 | 36 |
| 7 | 20 | 38 |
| 8 | 21 | 40 |

Ground is available at physical pin 39. Hold contacts closed for at least 50 ms. A contact held during boot does not trigger until opened and closed again.

## Verify and troubleshoot

```sh
python3 -m unittest discover -s tests -v
sudo systemctl status gpi-touchdown
sudo journalctl -u gpi-touchdown -n 100 --no-pager
```

The tests include simulated Ultrix, Videohub and ATEM protocol exchanges. Physical router and switcher validation is still required. The web interface uses HTTP and is intended for a trusted control network.

GPLv3. See [LICENSE](LICENSE) and [third-party notices](app/THIRD_PARTY.txt).
