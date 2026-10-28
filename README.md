# Morcano bridge for Blender

Send a model from Blender to a [Morcano](https://morcano.app) board in one click, then
press **Create generator** there: its parts become colours and switches, its text objects
become fields a customer fills in, and every variant is checked to print before anyone
gets a file.

## Install

Blender 4.2 or newer.

1. Download `morcano_bridge-0.2.0.zip` from the [releases](../../releases).
2. Blender: **Edit > Preferences > Get Extensions > ⌄ > Install from Disk**, pick the zip.
3. In the 3D Viewport press **N** and open the **Morcano** tab.

## Use

- **Check for print** lists, object by object, what would stop the print (open edges,
  empty meshes, too many triangles) and how to fix it in Blender. **Send** runs the same
  check first.
- **Send to Morcano** (or **Send selected**) opens the board in your browser with the
  model on it.

## What is sent - and what is not

Sent, only when you press Send:

- the shapes of the visible mesh and text objects (or the selected ones), as they show
  with modifiers on, in world space;
- their names, material names and colours, and parents;
- for each text object: its words, font name, size, alignment and place;
- the .blend's file name, as the model's name;
- a font file only when it is packed in the .blend, or when you tick **Send fonts**
  (off by default - a font's licence may not allow sending it).

Never sent: file paths, images, other data blocks, scripts, your account or computer
details. There is no telemetry and no background traffic.

It sends only to `https://morcano.app` over HTTPS, or to a studio on your own computer
(`http://localhost:PORT`) for testing. Any other address is refused.

## Licence

GPL-3.0-or-later, as Blender add-ons are. "Morcano" and its logo are trademarks of
Morcano and are not covered by this licence: a modified version must use another name.
