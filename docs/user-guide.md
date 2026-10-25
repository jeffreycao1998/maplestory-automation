# MapleBot user guide

MapleBot trains your magician in MapleStory Classic. It watches the game window and
presses keys, the same way you would. It doesn't touch the game's files or memory.

You don't need to know any programming. Setup takes about 10 minutes the first time.

For monster/drop setup, use **Download sprites for this map…** on the wizard's **Mobs**
step. Select the game's map name and download its animation poses and drop icons.
Drawing monster boxes is optional. Check the green matches and try **Observe only**
before starting with keys. See [sprite setup and its detection limits](assets.md).

> **Read this first.** Using a bot breaks Nexon's Terms of Service. Accounts on official
> servers **do get banned**, sometimes long after the fact. Only use it on an account
> you can afford to lose, and never leave it running unattended for long.

---

## 1. Install Python (one time)

1. Go to <https://www.python.org/downloads/> and click the yellow **Download Python** button.
2. Run the installer. On the first screen, **tick "Add python.exe to PATH"** at the bottom.
   This is the step people most often miss.
3. Click **Install Now** and wait until it finishes.

If you skip this, `start.bat` notices, opens the download page for you and tells you what to do.

## 2. Download MapleBot

- **Easiest:** on the project's GitHub page, click the green **Code** button, then
  **Download ZIP**. Right-click the ZIP and choose **Extract All...**. Put the folder
  somewhere simple like `Documents\MapleBot`. Don't use `Program Files`.
- **With git:** `git clone <repository URL>`

## 3. Start it

Double-click **`start.bat`** in the MapleBot folder.

- Windows asks **"Do you want to allow this app to make changes?"** Click **Yes**. The
  game runs as administrator, so the bot has to as well. Otherwise Windows silently
  blocks its key presses.
- **The first time**, a black window installs what the bot needs. This takes a few
  minutes. After that, start-up takes a few seconds.
- The **MapleBot** window then opens. If setup isn't done yet, the **setup wizard** opens
  automatically.

### Updating after installation

Stop the bot, then click **Check for updates** in the main window. Confirm the update;
the app downloads the code, makes a backup, closes, installs and reopens itself.
Your map setups, keybinds, templates, logs, recordings and downloaded assets stay in place.
An internet connection is needed. Details and recovery: [updating.md](updating.md).

Older copies need one final ZIP download to get this button. Download the
`main` branch. If you extract into a new folder, copy your old `profile/` and
any local `data/` assets into it before starting. Keep the old folder as a backup.
Future updates use the button.

## 4. Before the wizard: get the game ready

- Run MapleStory in **windowed mode**, not full screen.
- Log in and go to the map you want to train on.
- Make sure the **minimap is open**. It's the small map in the top-left corner.
- **Don't move or resize the game window** after setup. If you do, run the wizard again.

## 5. The setup wizard, step by step

The wizard has **Back** and **Next** buttons. Next tells you what's missing if a step
isn't done yet.

Whenever the wizard takes a screenshot, it **hides for 3 seconds** and a countdown
appears in the bottom-right corner. During those 3 seconds, **click on the game** so
it's in front and nothing covers it.

| Step | What to do | Tips |
|---|---|---|
| 1. Game window | The title is usually already right ("MapleStory"). Click **Find window**. You should see "Found it: game area is 1366 x 768". | If it can't find the window, check that the game isn't minimised. A private server's window may have a different name, so type part of it. |
| 2. Screenshot | Click **Take screenshot**, then click on the game. | Stand somewhere with a monster or two on screen. Steps 3 to 7 all use this picture. |
| 3. Minimap | **Drag a box** around the minimap. A cyan box shows what you picked. | Include the whole map area but not its title bar. Drag again to redo it. |
| 4. Your dot | The minimap is shown zoomed in. **Click your own yellow dot.** A green circle shows where the bot sees you. | Don't click party members' orange dots. If the circle isn't on your dot, click **Try again**. |
| 5. Mobs | Click **Download sprites for this map…**, select the game's map name, then **Download and use these sprites**. Continue with Next; mob boxes are optional. | Downloaded animation poses and drop icons are saved for this map. Green boxes show sprite matches. Use **Observe only** before enabling keys. If a pose is missed, you can optionally add a tight screenshot example. |
| 6. Your name tag | Drag a tight box around the **name tag under YOUR character**. The bot uses it to know exactly where you are. **This step is required**: without it, the bot only guesses your position and won't attack. | Box just your name, not other players' tags or your guild name. If you change characters, redo this step. |
| 7. HP and MP bars | Pick **HP bar** and drag along the red bar. **This one is required**: the bot uses it to stop before your character dies. Then do the same for the blue **MP bar**. The wizard shows how full it reads each bar, e.g. "HP reads 43% full". | The MP bar is optional but **strongly recommended for magicians**: you run out of MP fast, and without it the bot can't drink MP potions. Check the numbers roughly match the game. |
| 8. Training area | Walk your character to the **left end** of where it should train. Click **Set left** and click the game during the countdown. Then walk to the **right end** and click **Set right**. The wizard also records how high that bridge is on the minimap. | **Pick one bridge, set both edges on that same bridge, a little in from its ends.** On maps like The Tree That Grew II, with a stack of sagging rope bridges, the wizard warns "those two spots look like different bridges" if your two readings are at different heights. You can fine-tune both numbers in the boxes (0 = far left of the minimap, 1 = far right). |
| 9. Keys | Click **Press a key**, then press your **attack skill** key. Do the same for **jump**, and optionally your **HP** and **MP potion** keys. You can also choose from the dropdown. Then set the skill's **reach** with the slider and the **time between casts**. | Magic Claw is about **0.6–0.7 s** between casts. Start with a slightly short reach and check it in the live preview. Each key must be different. |
| 10. Done | Check the summary and click **Save**. | You can change anything later in **Settings**, or run the wizard again. |

## 6. Running the bot

The main window stays on top of the game (untick **Keep on top** if you don't want that).

1. Click **Start**.
2. A countdown appears ("Starting in 3... click into the game"). **Click on the game
   window** so it has keyboard focus.
3. The bot starts training.

| Control | What it does |
|---|---|
| **F11** (or the **Pause** button) | Pause or resume. All keys are released straight away. |
| **F12** (or the **Stop** button) | Stop the bot. |
| Closing the window | Also stops the bot. |

F11 and F12 work even while the game is in front. You can change them in Settings.
If they can't be used on your PC, a red **"F11/F12 not working — use the buttons"** badge
stays in the main window. Use the Pause and Stop buttons instead.

Only one copy of MapleBot can be open at a time. If you double-click `start.bat` while
it's already open, it tells you so instead of opening a second copy.

The **Status** box shows what the bot is doing: its state (PATROL = walking around,
ENGAGE = attacking, RECOVER / STUCK = sorting itself out, WAIT = can't see enough),
mobs seen, your position on the minimap, HP and MP, number of attacks, run time, and
the last message, such as why it paused.

### The live preview

Tick **Preview** to see what the bot sees:

| Colour | Means |
|---|---|
| **Green** circle and box | You, and how far your skill reaches. **Amber** means the bot can't find your name tag right now and is guessing. It won't attack until it finds you again. |
| **Orange** boxes | Monsters it found |
| **Red** box | The monster it's attacking now |
| **Cyan** box | The minimap |
| **Pink** lines (on the minimap) | The left and right ends of your training area |

### The bot pauses itself when something's wrong

The bot would rather stop than guess. It pauses when it can't see your dot (map change,
death, a popup), when it sees no monsters for a long time, or when it keeps getting
stuck. It stops completely if HP gets critically low or the time limit is reached.
Check the game, fix the problem, then press **F11** or **Resume**.

## 7. Training on more than one map

MapleBot keeps a separate setup for each map. Your keys, skill and safety settings are
shared by all of them.

- **Pick the map before you press Start.** Use the **Map** dropdown at the top of the
  main window. A map marked **(not set up)** isn't ready yet. Its Start button says
  **Run the setup wizard for this map**.
- **New map…** asks for a name (e.g. *The Tree That Grew II*) and opens the wizard
  straight away. Your game window, dot colour, name tag, HP/MP bars and keys are kept
  from the map you were on. Those steps say **"Kept from your other map"**: click
  **Next** to keep them, or **Redo** to do them again. You only need to redo the
  **minimap box**, the **mobs** and the **training area**.
- **Tip:** set a map up while your character is standing on that map, so the
  screenshot shows its minimap and monsters.
- **Rename…** changes the name shown in the list. **Delete** removes a map's setup,
  including its mob pictures, after asking you first. You can't delete your only map.
- You can't switch maps while the bot is running. Stop it first.

## 8. Troubleshooting

| Problem | What to do |
|---|---|
| The bot runs but **keys do nothing** in the game, or "Can't press keys in the game" | Close MapleBot and start it with **`start.bat`**, clicking **Yes** to the admin question. The main window shows a red warning when it's not running as admin. Also click into the game after pressing Start. |
| **"Can't see your character"** and it pauses | The yellow dot wasn't found. Is the minimap open? Did the window move? Run the wizard again (steps 2 to 4). |
| **No mobs detected** (no orange boxes) | Add more mob pictures in the wizard (step 5), with tight boxes and different poses. Or lower **Mob match strictness** in Settings → Detection (try 0.6). |
| It finds things that **aren't monsters** | Raise **Mob match strictness** a little (try 0.75), and redo mob pictures with tighter boxes. |
| It **attacks monsters on other platforms** | Lower **Up/down tolerance** in Settings → Attack skill. |
| It **walks off the edge** | Run step 8 again, or nudge **Patrol left/right edge** inward in Settings → Screen setup. |
| It **attacks too early or too late** | Adjust **Skill reach** (Settings → Attack skill) until the green box in the preview matches where your skill actually hits. |
| It **casts too slowly or presses too often** | Adjust **Time between casts**. |
| It **walks around the wrong map / can't see you** after changing maps | Pick the map you're actually on in the **Map** dropdown before pressing Start. |
| **"Game window not found"** | The game must be open, windowed and not minimised. Check the window title in Settings → Screen setup. |
| The **wizard screenshot is black or blank** | Don't minimise the game. Click on it during the countdown so nothing covers it. |
| **Potions aren't used** | Set the HP/MP bar boxes (wizard step 7) and the potion keys (step 9). |
| **"Those two spots look like different bridges"** | You set the left and right edges on different bridges. Redo step 8, standing on the same bridge both times. Both ends of one bridge are at the same height, so this check is strict (**Platform tolerance above** in Settings → Screen setup). |
| It thinks it **fell off the platform** while it's still on the bridge | Raise **Platform tolerance below** a little (Settings → Screen setup). Bridges sag in the middle. |
| It **patrols but never attacks** | It can't find your name tag. Redo wizard step 6 with a tight box around just your name. In the preview, your circle should be **green**, not amber. |
| The app **doesn't open** at all | Run `start.bat` again. It shows the error in a black window if the app can't start. If installing failed, delete the `.venv` folder and run `start.bat` again. |

**Logs:** click **Open logs folder** in the main window. `app.log` has the app's messages,
and each run has its own folder with details and pictures of any problems. Include them
when you ask for help.

**Your setup** lives in the `profile` folder: `settings.yaml` holds the shared settings,
and `maps\<map>\` holds each map's screen setup and its mob and name tag pictures. Back
it up if you like. Delete it to start fresh.


## Navigation, pickup and reference packs

See [navigation.md](navigation.md) for ramps, platform links, down+jump, spell timing,
edge braking, looting and the automatic minimap/bar setup buttons. See
[assets.md](assets.md) for bulk sprite and map-data import without boxing every item.

For visible potion/wallet OCR, Observe mode, ladders, movement measurements, real-frame
reference learning and economy reports, see [reliability.md](reliability.md).
