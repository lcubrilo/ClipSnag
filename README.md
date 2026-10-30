# ClipSnag

Kopiraš link videa, dodirneš pločicu u Quick Settings, iskoči notifikacija **„Video link spreman za skidanje“** sa dugmićima **Skini / Ne skini**, izabereš kvalitet, i video je u `Download/ClipSnag`.

Radi za Instagram, YouTube, TikTok, X/Twitter, Facebook, Vimeo, Reddit i [još ~1800 sajtova](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md), jer iza svega stoji [yt-dlp](https://github.com/yt-dlp/yt-dlp).

## Instalacija

1. Na telefonu otvori [najnoviji Release](../../releases/latest) i skini **ClipSnag.apk**.
2. Otvori fajl. Android će tražiti da dozvoliš instalaciju iz tog izvora (Chrome/Files), dozvoli.
3. Otvori ClipSnag jednom, dozvoli notifikacije i dodirni **„Dodaj pločicu u Quick Settings“**.

Automatska ažuriranja: dodaj URL ovog repoa u [Obtainium](https://github.com/ImranR98/Obtainium).

## Kako se koristi

| Način | Šta radiš |
|---|---|
| **Pločica** (kao Shazam) | Kopiraj link → povuci Quick Settings → **Skini video** → notifikacija → **Skini** → izaberi kvalitet |
| **Podeli** | U Instagramu/YouTube-u: Podeli → **ClipSnag** → izaberi kvalitet |
| **Aplikacija** | Otvori ClipSnag, link iz clipboarda je već nalepljen → **Izaberi kvalitet i skini** |

Zašto pločica, a ne automatsko praćenje clipboarda: od Androida 10 aplikacije u pozadini ne smeju da čitaju clipboard. Pločica je jedan dodir i ne traži nikakve posebne dozvole.

## Kako je napravljeno

Cela aplikacija je u **Pythonu** ([Chaquopy](https://chaquo.com/chaquopy/)): activity-ji, Quick Settings pločica, servis za skidanje i notifikacije su Python klase (`app/src/main/python/clipsnag/components.py`) koje Chaquopy pretvara u Java klase.

- `core.py`: prepoznavanje linka, lista kvaliteta, skidanje (yt-dlp), samoažuriranje yt-dlp-a. Čist Python, testovi u `tests/`.
- `droid.py`: notifikacije, clipboard, čuvanje u `Download/` preko MediaStore-a, spajanje slike i zvuka preko Androidovog `MediaMuxer`-a (bez ffmpeg-a, bez ponovnog enkodovanja).
- `components.py`: Android komponente.

yt-dlp se ažurira sam (jednom dnevno proveri GitHub) jer sajtovi stalno menjaju format. Ručno: dugme **Ažuriraj yt-dlp**.

## Build

Svaki push na `main` pokreće GitHub Actions: testove, build APK-a, test na Android emulatoru (pravo skidanje) i novi Release.

Lokalno: Android SDK, JDK 17, Python 3.13 i Gradle 8.14, pa `gradle :app:assembleRelease`.

## Ograničenja

- Instagram često traži prijavu za reels/storije. Javni postovi uglavnom rade, ali Instagram ponekad blokira anonimne zahteve.
- YouTube povremeno menja zaštitu. Ako skidanje prestane da radi, pritisni **Ažuriraj yt-dlp**.
- Nije na Google Play-u: Play politika zabranjuje aplikacije koje skidaju sa YouTube-a.
- Skidaj samo sadržaj koji imaš pravo da sačuvaš.
