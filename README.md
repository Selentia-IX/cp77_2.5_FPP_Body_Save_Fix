# Cyberpunk 2077 2.5: First-Person Body Save Fix

Fixes the missing first-person torso on characters created before patch 2.5.

## The problem

If your character was made before 2.5, you may not see your torso and thighs when you look down in first person.
Inventory and photo mode still show the full body. It happens even with every mod removed, and a new 2.5 game is
fine.

## The cause

The save stores the character's look in the node `CharacetrCustomization_Appearances` (CDPR's spelling). It has a
first-person body group, `FPP_Body`, and a third-person one, `TPP_Body`.

| Group      | Save from a 2.5 character                          | Save from a pre-2.5 character |
|------------|----------------------------------------------------|-------------------------------|
| `TPP_Body` | `body_color`, `body_color_censored`, `underpants`  | same                          |
| `FPP_Body` | same as `TPP_Body`                                 | `underpants` only             |

Without a `body_color` entry the game has no first-person torso look to apply, so nothing is drawn. Loading and
re-saving an old character on 2.5 doesn't upgrade the group.

## The fix

The tool copies the save's own `TPP_Body` entries into `FPP_Body`, the way a 2.5 save has them. Your character's
own skin tone and options are used; nothing is invented.

## Usage

You need **Python 3.8 or newer** ([python.org](https://www.python.org/downloads/)). Nothing else.

1. Back up your saves folder:
   `%USERPROFILE%\Saved Games\CD Projekt Red\Cyberpunk 2077`
   (paste that into the File Explorer address bar)
2. Find the save you want to fix, for example `ManualSave-3`. The newest one is usually best.
3. Drag that save **folder** onto `Fix first-person body.bat`.
4. Load the new save it makes (`ManualSave-50`, or the next free number) and look down in first person.
5. Keep playing from the fixed save. Saves you make from it stay fixed.

Or from a terminal:

```
python fpp_body_fix.py "<path to save folder>" [more save folders...]
```

## Safe by design

- **The original save is never changed.** The fixed copy is written as a new save next to it; existing saves are
  never overwritten.
- **Only that one group changes.** Every other part of the save is kept byte for byte, and the tool re-reads its own
  output and checks it before writing anything.
- **Saves that are already fine** (made on 2.5) are left alone.
- **Unfamiliar save layouts are refused** without writing anything.

## Tested

- Confirmed in game on female V saves: the converted save loads, the torso shows in first person, and saves made
  from it stay fixed.
- Male V and other setups haven't been tested yet. If it doesn't work for you, delete the new save it made (your
  original is untouched) and open an issue with your body type, mods and the tool's message.

## Save format notes

For anyone building save tools. Found by reading 2.5 saves; no checksum of the data was found.

- `sav.dat` starts with `CSAV` (stored as `VASC`), version 277 on 2.5.
- Then an `FZLC` chunk table: offset, compressed size and size per chunk, with room for 512 entries.
- Chunks are LZ4 blocks of 0x40000 bytes, each prefixed `4ZLX` + the uncompressed size.
- The node table (`EDON`) follows the last chunk: per node a packed name, two links, an offset and a size. Offsets
  count from the start of the file.
- The file ends with the node table's offset and `ENOD`.
- In `CharacetrCustomization_Appearances`, a body group is a packed name, a `u32` entry count, then per entry: an
  8-byte resource hash, the look name, the option name and two `u32` values.

## Credits

Thanks to the community members who reported and investigated the missing first-person body.
