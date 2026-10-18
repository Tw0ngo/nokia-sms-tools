# Nokia logo & ringtone tools

Remember when you could change the network name on your Nokia's start screen into a little
picture, or get a new ringtone by SMS? Old Nokias (3310, 7110, 6210, 8210, 3410 and friends) still
understand those messages. This lets you make your own and send them.

## Just making logos and ringtones

Download **[`web/index.html`](web/index.html)** (open it, then "Download raw file") and open it
in your browser. That's it: no installing, no account, works offline.

**Operator logo**: paint a 72×14 pixel picture, see right away how it looks on the phone, or
start from one of 47 classic logos from back in the day. Download it as `.bmp`.

**Ringtone**: drop in a MIDI file and it picks out the melody for you, or play one in on the
on-screen piano (your computer keyboard works too). Listen to it, fix notes, and see whether it
still fits into one SMS. Download it as RTTTL text (the classic ringtone format) or as `.wav`.

## Actually sending them to a phone

For that you need a **second old phone that can send SMS from a computer**, and a computer that
stays on, like a Raspberry Pi or an old laptop with Linux.

What you need:
- **A phone connected by USB that works as a modem.** Tested with a Nokia 6230i. Most Nokias with a
  USB data cable from the mid-2000s should do, many other phones too. It needs a SIM card with credit:
  **every logo or ringtone you send costs as much as a normal SMS**, paid by that SIM.
- **The phone that receives it**: the old Nokia you want to put the logo or ringtone on.
  Tested with a Nokia 7110.
- **A computer with Linux** (e.g. Raspberry Pi OS, Debian, Ubuntu).

### Setting it up

1. Plug the sending phone into the computer and switch it on.
2. Download this project: click the green **Code** button above → **Download ZIP**, unpack it,
   and open a terminal in that folder. Or with git: `git clone https://github.com/Tw0ngo/nokia-sms-tools`
3. Run:
   ```
   sudo ./install.sh
   ```
4. At the end it shows you an address like `http://192.168.1.20:8095`. Open it in a browser on
   any computer or phone in your home network. It's the same page as above, now with a
   **"Send it to a phone"** box under the logo and the ringtone.

### Settings

All settings are in one file, with explanations right next to them:
```
sudo nano /etc/nokia-sms/config.ini
sudo systemctl restart nokia-sms      # afterwards, so the changes count
```

What you can set there:

| Setting | What it does |
|---|---|
| `home_country` | Your country's calling code (e.g. `49` for Germany), so people can type numbers the way they always do, `0151…` instead of `+49 151…`. |
| `allow` | Only send to these numbers. You can list whole countries (`+49 +43`) or just your friends' numbers. Empty means anyone. |
| `block` | Never send to these numbers, e.g. expensive service numbers, or someone who doesn't want any more SMS. |
| `sms_per_day` | How many SMS all together per day. Your SIM pays for them, so the default is 20. |
| `sms_per_number_per_day` | How many per receiving number per day (default 3), so nobody gets spammed. |
| `password` | If you set one, people need it to send. Painting and downloading still work without it. A must if you put the page on the internet. |
| `networks` | The networks offered in a list for logos (see below). People can always type in a code themselves. |

The server doesn't save phone numbers. To count the per-number limit it only keeps a scrambled
fingerprint that can't be turned back into the number and is thrown away every night.

### Why the logo needs the "network"

An operator logo is always meant for one network. The phone only shows it if it matches the
network of its SIM card, otherwise it just ignores it. That's why the page asks which network
the receiving phone is on. The code has two parts, the country and the network, e.g. `262 01`
for Telekom in Germany. You can find yours on
[this list](https://en.wikipedia.org/wiki/Mobile_country_code) or in the phone's network
selection menu. Ringtones work on any network.

### When something doesn't work

- **"No phone found"**: is the phone switched on and plugged in? `ls /dev/serial/by-id/` should list
  it. If there are several, put the right one under `device` in the settings.
- **"The phone didn't manage to send it"**: is there credit on the SIM? Does the phone have signal?
  `journalctl -u nokia-sms -n 30` shows what the phone answered.
- **The logo arrives but doesn't show up**: the network code doesn't match the receiving SIM.
- **The ringtone doesn't arrive**: it has to fit into one SMS. The bar under the ringtone shows how full it is.

## For tinkerers: command-line tools

The [`cli/`](cli) folder has the same things for the terminal, sending with
[gammu](https://wammu.eu/gammu/) (`sudo apt install gammu console-setup-linux`):

```sh
cd cli
./makelogo.py --text "HELLO" -o logo.bmp                 # logo from text
./makelogo.py --art examples/vorlage.art -o logo.bmp     # from a text drawing: 14 lines of 72 characters, # = black
./compose.py -o logo.bmp art:examples/stern.art gap:4 text:HELLO     # picture and text side by side
sudo ./send-logo.sh +4915112345678 "262 01" logo.bmp     # number, network of the receiving SIM, logo

./midi2rtttl.py song.mid -o song.txt --wav song.wav      # MIDI to ringtone (--list shows the tracks)
sudo ./send-ringtone.sh +4915112345678 song.txt
```

They use the same phone as the web page. If one of them says the phone is busy, just try again
a moment later.

## License

MIT: do whatever you like with it, just keep the [LICENSE](LICENSE) file.
