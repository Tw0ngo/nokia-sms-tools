# Nokia logo & ringtone tools

Tools for creating operator logos and ringtones for older Nokia phones and sending them by SMS
(Nokia Smart Messaging). Supported by most Nokia models of the late 1990s and early 2000s,
e.g. 3310, 3410, 6210, 7110, 8210.

## Creating logos and ringtones

Download [`web/index.html`](web/index.html) ("Download raw file" on the file page) and open it in a
web browser. No installation is required and it works offline.

- **Operator logo:** a 72×14 pixel editor with a phone display preview and 47 classic logos as
  templates. Export as `.bmp` or `.art` (text).
- **Ringtone:** import a MIDI file (the melody is selected automatically) or enter notes on the
  on-screen or computer keyboard. Includes playback, a piano-roll editor and a check whether the
  ringtone fits into a single SMS. Export as RTTTL text or `.wav`.

## Sending them by SMS

Sending requires additional hardware:

- **A sending phone** connected by USB that can be used as a GSM modem (tested: Nokia 6230i;
  most phones with a USB data cable should work). Its SIM card pays for the messages: each logo
  or ringtone is charged as one regular SMS.
- **A receiving phone** that supports operator logos or ringtones by SMS (tested: Nokia 7110).
- **A Linux computer** that stays switched on, e.g. a Raspberry Pi.

### Installation

1. Connect the sending phone by USB and switch it on.
2. Download the project (**Code → Download ZIP** and unpack it, or
   `git clone https://github.com/Tw0ngo/nokia-sms-tools`) and open a terminal in the folder.
3. Run `sudo ./install.sh`.
4. The installer prints the address of the web page, e.g. `http://192.168.1.20:8095`. It can be
   opened from any device on the local network and contains the same tools, plus a form to send
   the logo or ringtone.

### Configuration

The settings are in `/etc/nokia-sms/config.ini`; each option is described in the file. Apply
changes with `sudo systemctl restart nokia-sms`.

| Option | Description |
|---|---|
| `home_country` | Country calling code (e.g. `49`). Allows numbers in national format (`0151…`). If empty, numbers must be entered with `+`. |
| `allow` | Only send to numbers starting with these prefixes, e.g. whole countries (`+49 +43`) or individual numbers. Empty: no restriction. |
| `block` | Never send to numbers starting with these prefixes, e.g. premium-rate numbers or people who have opted out. Takes precedence over `allow`. |
| `sms_per_day` | Maximum number of SMS per day in total (default 20). `0` disables the limit. |
| `sms_per_number_per_day` | Maximum number of SMS per receiving number per day (default 3). `0` disables the limit. |
| `password` | Optional. If set, it is required for sending; the editors remain usable without it. Recommended if the page is reachable from the internet. |
| `networks` | Networks listed in the logo form. Other network codes can always be entered manually. |
| `device` | The phone's serial port. `auto` detects it; set it manually if several devices are connected. |
| `listen` | Address and port of the web server (default `0.0.0.0:8095`, i.e. reachable on the local network). |

Phone numbers are not stored. The per-number limit uses a keyed hash whose key is replaced daily.

### Network codes for operator logos

An operator logo is tied to a network and is only displayed if it matches the network of the SIM
card in the receiving phone. The network is identified by its country code (MCC) and network code
(MNC), e.g. `262 01` for Telekom Germany. Codes are listed on
[Wikipedia](https://en.wikipedia.org/wiki/Mobile_country_code) and usually shown in the phone's
network selection menu. Ringtones are not tied to a network.

### Troubleshooting

- **"No phone found":** check that the phone is switched on and connected; `ls /dev/serial/by-id/`
  should list it.
- **Sending fails:** check the SIM credit and signal. `journalctl -u nokia-sms -n 30` shows the
  phone's response.
- **Logo received but not displayed:** the network code does not match the receiving SIM card.
- **Ringtone not received:** it must fit into one SMS; the editor shows the current size.

## Command-line tools

The [`cli/`](cli) folder contains equivalent tools for the terminal. Sending uses
[gammu](https://wammu.eu/gammu/); install the dependencies with
`sudo apt install gammu console-setup-linux`.

```sh
cd cli
./makelogo.py --text "HELLO" -o logo.bmp                          # logo from text
./makelogo.py --art examples/vorlage.art -o logo.bmp              # from a text drawing (14 lines of 72 characters, # = black)
./compose.py -o logo.bmp art:examples/hammer-sichel.art gap:6 text:HELLO   # image and text combined
sudo ./send-logo.sh +4915112345678 "262 01" logo.bmp              # number, network of the receiving SIM, logo

./midi2rtttl.py song.mid -o song.txt --wav song.wav               # MIDI to RTTTL (--list shows the tracks)
sudo ./send-ringtone.sh +4915112345678 examples/internationale.txt
```

The command-line tools and the web server use the same phone. If a send fails because the phone
is busy, retry after a moment.

## License

MIT, see [LICENSE](LICENSE).
