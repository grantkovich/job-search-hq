# Your device: Windows, Mac, Chromebook, iPhone and Android

Job Search HQ is a website, so nothing is downloaded from an app store and nothing is installed on your computer. This guide covers the parts that differ by device:

- **[Part 1: Setting up from your computer](#part-1-setting-up-from-your-computer):** for the helper, who runs the setup once from a Windows PC, Mac or Chromebook.
- **[Part 2: Using Job Search HQ on each device](#part-2-using-job-search-hq-on-each-device):** for the job seeker. It covers opening it, installing it as an app, notifications and the microphone, on every device.
- **[Part 3: Device problems](#part-3-device-problems)**

New to all this? Read [start-here.md](start-here.md) first. It explains the words used here.

**Menu names change.** Browsers and operating systems rename their menus now and then. If a step doesn't match what you see, look for the closest wording, or search the device's help for the step's name. These steps were written from Google's, Apple's and Microsoft's own help pages in November 2026.

---

# Part 1: Setting up from your computer

The setup runs in **Cloud Shell**, a text window inside a browser tab that Google provides. Because it's in the browser, the setup is the same on a Windows PC, a Mac and a Chromebook. Only the keys, and how you place windows side by side, differ.

**Use a personal computer if you can.** Work and school computers, and especially managed Chromebooks, often block Google Cloud or Cloud Shell. If a page says your administrator has blocked it, use another device.

**Phones and tablets:** you can use Job Search HQ on them, but don't run the setup there. Cloud Shell needs a keyboard and a large screen. An iPad or Android tablet with a keyboard can work, but it's not tested.

## Before you start, on every computer

1. **Use Chrome for the setup.** Safari is fine for using Job Search HQ day to day, but use Chrome (or Edge on Windows) for Cloud Shell. Update it first: Chrome's menu **⋮ → Help → About Google Chrome** installs any update.
2. **Sign in to Chrome, or a browser window, with the Google account you'll use for Google Cloud.** If you use several Google accounts, consider a separate Chrome profile for this: click your picture at the top right of Chrome, then **Add**. Mixing accounts is the most common source of confusing errors.
3. **Turn off private or incognito browsing** for the setup. Cloud Shell needs to remember you, and private windows block that.
4. **Plug in the charger and keep the lid open** for the setup. There's one long wait of 10 to 25 minutes, right after you paste the Client secret, while the app is built on the server. The screen may not change during it; that's normal. Cloud Shell closes after 40 minutes with no activity.
5. **Use a personal Google account,** not a school or work one, which usually block Google Cloud.
6. **Have the two other things ready:** your card for the Google Cloud trial, and your Claude account or API key ([install.md, steps 1 and 2](install.md#1-google-cloud-account)).

## Copying and pasting

You'll paste a command into Cloud Shell, and later copy codes from one Google page into the setup. Here are the keys:

| | Windows | Mac | Chromebook |
|---|---|---|---|
| **Copy** selected text on a web page | Ctrl + C | ⌘ Command + C | Ctrl + C |
| **Paste** into a web page or into Cloud Shell | Ctrl + V | ⌘ Command + V | Ctrl + V |
| **Copy from Cloud Shell's text window** | Select the text with the mouse first, then Ctrl + C | Select, then ⌘ Command + C | Select the text first, then Ctrl + C |
| **Switch tabs** | Ctrl + Tab | ⌘ Command + Option + → | Ctrl + Tab |

**Two cautions in Cloud Shell:**
- **Select first, then Ctrl + C.** In a terminal, Ctrl + C with nothing selected stops whatever is running. If that happens by mistake during setup, nothing is lost: run the setup again (see [If Cloud Shell closes or stops](#if-cloud-shell-closes-or-stops)). On a Mac, ⌘ Command + C never stops anything.
- **Your browser may ask to allow pasting** the first time. Choose **Allow**.

## Two windows side by side

During the Google sign-in step, you go back and forth between Cloud Shell and the Google Cloud console. It's easiest with the two side by side. The setup opens each Google page from a link: hold **Ctrl** (Windows, Chromebook) or **⌘ Command** (Mac) and click the link to open it in a new tab, then drag that tab out into its own window.

- **Windows:** click one window and press **Windows key + ←**, then pick the other window for the right half. Or drag a window's title bar to the left or right edge of the screen.
- **Mac:** hover over the green button at a window's top left and choose **Tile Window to Left of Screen**, then pick the other window for the right half. On older macOS, hold the green button and choose **Tile Window to Left of Screen**.
- **Chromebook:** click one window and press **Alt + [** to dock it on the left, then click the other and press **Alt + ]** to dock it on the right.

If your screen is small, tabs work too. Just switch with the keys above.

## Opening Cloud Shell

1. Go to **[shell.cloud.google.com](https://shell.cloud.google.com)**.
2. **The first time,** Google may ask you to accept the Cloud Shell terms. Tick the box and choose **Start Cloud Shell**, or **Continue**.
3. Wait for a black or white text window with a line ending in `$`. That's the prompt: it's waiting for a command.
4. **A box called "Authorize Cloud Shell"** may appear once the setup starts using Google Cloud. Choose **Authorize**. It lets the setup act on your Google Cloud account, in this tab only.

## Running the setup

1. **Click once in the text window,** then paste this line and press **Enter**:
   ```
   git clone https://github.com/grantkovich/job-search-hq.git && cd job-search-hq && bash scripts/install.sh
   ```
2. **Answer each question.** Most are numbered menus; a few, like names, email addresses and the codes from Google, you type or paste:
   ```
   Server size:
     1) Trial: e2-medium, about $25 a month, which the $300 trial credit covers. Voice practice on.
     2) Forever free: e2-micro on Google's free tier. Voice practice off. Needs an upgraded billing account.
   Size [1]:
   ```
   Type the number and press **Enter**. The number in brackets is the suggested answer: press **Enter** alone to accept it. If an answer doesn't fit, the setup says why and asks again.
3. **When the setup prints a link,** hold **Ctrl** (or **⌘ Command**) and click it to open it in a new tab. When it says **Press Enter when…** (for example "Press Enter when Part 1 is done"), finish that step in the other tab, then come back and press **Enter**.
4. **When it asks for the Client secret,** paste it and press **Enter**. **Nothing appears as you paste.** That's on purpose, so nobody can read it over your shoulder. Paste once only, then press **Enter**. If the paste looks wrong, for example pasted twice, the setup says so and asks again.
5. **The long wait** comes right after the Client secret: "Building and starting …'s copy" takes 10 to 25 minutes with nothing changing on screen. Don't press anything until it moves on to `7/7  Claude`.
6. **When it signs in to Claude Code** (if you chose a Claude plan), it prints a long link. Select all of it with the mouse (it may wrap onto several lines) and copy it, open it in a new tab, sign in to the job seeker's own Claude account, and copy the code it shows. Back in Cloud Shell, paste the code and press **Enter**.
7. **At the end** it prints "Done. Next:" and the web address. Open it in a new tab and choose **Sign in with Google**.

[install.md](install.md) explains every question and every Google page in detail.

## If Cloud Shell closes or stops

The setup is safe to run again. It finds what it already made and carries on.

1. Open **[shell.cloud.google.com](https://shell.cloud.google.com)** again.
2. Paste this line and press **Enter**:
   ```
   cd job-search-hq && git pull && bash scripts/install.sh
   ```
   If it says `No such file or directory`, paste the line from [Running the setup](#running-the-setup) instead.
3. **Give the same answers as before.** At **Project**, the suggested answer is the one whose name starts with `jobhq-`: press **Enter** to keep it, and never choose "Create a new project". Pick the same size and region too, so it finds the server it already started rather than making a second one.

Cloud Shell gives each account 50 free hours a week, far more than the setup needs, and keeps your files between visits.

## Advanced: running the setup from your own terminal

You don't need this; Cloud Shell is simpler. If you'd rather use your own computer's terminal:

- **Mac:** install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install), run `gcloud init` in the Terminal app, then run the same `git clone` line. The setup works with the Mac's built-in tools. The first time, macOS may offer to install its command line developer tools for `git`; choose **Install** and run the line again when it's done.
- **Linux:** the same, with the Google Cloud CLI for Linux.
- **Windows:** use Cloud Shell. The setup is a bash script, and Windows' own terminals don't run it. It may work under the Windows Subsystem for Linux with the Linux version of the Google Cloud CLI, but that isn't tested.
- **Chromebook:** use Cloud Shell.

---

# Part 2: Using Job Search HQ on each device

You'll get a web address like `https://pat.jobs.example.com` from your helper, or from the setup if you did it yourself. These steps are for the job seeker, on each device you'll use.

**On every device, do these four things once:**
1. **Open your address** and choose **Sign in with Google**, with the Google account your copy was set up for.
2. **Install it as an app**, so it opens in its own window with its own icon.
3. **Turn on notifications:** on the **Setup** page, choose **Turn on notifications**, allow them when the browser asks, then choose **Send a test**.
4. **Allow the microphone** the first time you record an interview answer, if you want to practice out loud.

The sections below show how on each device.

## Windows PC

**Best browsers:** Chrome or Edge.

**Install it as an app:**
- **Chrome:** click the **Install** icon at the right end of the address bar (a small screen with a down arrow). Or open the menu **⋮ → Cast, save, and share → Install page as app**, then **Install**.
- **Edge:** open the menu **… → Apps → Install this site as an app** (on some versions it's under **More tools → Apps**), then **Install**.
- **Then pin it:** right-click Job Search HQ on the taskbar and choose **Pin to taskbar**, or find it in the Start menu.

**Notifications:**
1. Choose **Turn on notifications** on the Setup page, then **Allow** in the box the browser shows.
2. If the test doesn't arrive, open Windows **Settings → System → Notifications**. Check that **Notifications** is on, and that **Google Chrome** or **Microsoft Edge** (or **Job Search HQ**, if listed) is on.
3. **Do not disturb** and **Focus** hide notifications. Turn them off on the same Settings page, or from the clock at the bottom right.

**Microphone:**
1. Choose **Allow** when the browser asks.
2. If it doesn't work, open Windows **Settings → Privacy & security → Microphone**. Turn on **Microphone access**, **Let apps access your microphone** and **Let desktop apps access your microphone**.
3. Check the right microphone is chosen in **Settings → System → Sound → Input**.

**Signing in with the wrong account:** if Chrome is signed in to a different Google account, use a separate Chrome profile for your job search. Click your picture at the top right, then **Add**.

## Mac

**Best browsers:** Chrome, or Safari on macOS Sonoma 14 or later.

**Install it as an app:**
- **Chrome:** click the **Install** icon at the right end of the address bar, or open **⋮ → Cast, save, and share → Install page as app**, then **Install**. It appears in Launchpad and your Applications folder.
- **Safari:** choose **File → Add to Dock** (or the **Share** button, then **Add to Dock**), keep the name and choose **Add**.
- **Keep it in the Dock:** while it's open, right-click its Dock icon and choose **Options → Keep in Dock**.

**Notifications:**
1. Choose **Turn on notifications** on the Setup page, then **Allow**.
   - **Safari web app:** allow notifications inside the Job Search HQ app window, not in a Safari tab.
2. If the test doesn't arrive, open the **Apple menu → System Settings → Notifications**. Find **Google Chrome** (or **Job Search HQ**, if listed) and turn on **Allow notifications**. Choose **Alerts** if you want them to stay on screen until you dismiss them.
3. **Focus** modes, such as Do Not Disturb, hide notifications. Check them in **Control Center**, at the top right of the screen.

**Microphone:**
1. Choose **Allow** when the browser asks.
2. If it doesn't work, open **System Settings → Privacy & Security → Microphone** and turn on **Google Chrome** (or **Safari**, or **Job Search HQ**). You may need to quit and reopen the browser.

## Chromebook

**Browser:** Chrome, which is built in.

**Install it as an app:**
1. Click the **Install** icon at the right end of the address bar, or open **⋮ → Cast, save, and share → Install page as app**, then **Install**.
2. It opens in its own window and appears in the Launcher (the circle at the bottom left).
3. **Pin it to the shelf:** right-click its icon on the shelf (the bar along the bottom; tap with two fingers on a touchpad) and choose **Pin**.

**Notifications:**
1. Choose **Turn on notifications** on the Setup page, then **Allow**.
2. If the test doesn't arrive, click the **clock** at the bottom right and make sure **Do not disturb** is off.
3. Then open **Settings → Apps → Notifications** and check **Job Search HQ** is turned on, if it's listed.

**Microphone:**
1. Choose **Allow** when Chrome asks.
2. If it doesn't work, open **Settings → Privacy and security → Privacy controls** and make sure **Microphone access** is on.

**School or work Chromebooks** may block installing apps, notifications or the microphone. The website still works in a tab.

## iPhone and iPad

**Browser:** Safari. Notifications need iOS or iPadOS 16.4 or later (**Settings → General → About** shows your version).

**Install it as an app (required for notifications):**
1. Open your address in **Safari**.
2. Tap the **Share** button (a square with an arrow pointing up). On an iPhone it's at the bottom of the screen; on an iPad it's at the top.
3. Scroll down and tap **Add to Home Screen**, then **Add**.
4. **From now on, open Job Search HQ from its home screen icon,** not from Safari. Sign in again there the first time.

**Notifications:**
1. In the home screen app, open **Setup**, tap **Turn on notifications**, then **Allow**.
2. If the test doesn't arrive, open **Settings → Notifications → Job Search HQ** and turn on **Allow Notifications**.
3. **Focus** modes, such as Do Not Disturb or Sleep, hide notifications. Swipe down from the top right to check.
4. **If you tapped "Don't Allow" by mistake,** delete the home screen icon (press and hold it, then **Remove App → Delete**), add it again, and turn notifications on again.

**Microphone:** tap **Allow** the first time you record an answer. If you tapped **Don't Allow**, it will ask again next time in most cases; if not, remove and re-add the home screen icon.

## Android phone or tablet

**Browser:** Chrome.

**Install it as an app:**
1. Open your address in **Chrome**.
2. Tap the menu **⋮** at the top right, then **Install app** (on some versions it's **Add to Home screen**, or **Install and create shortcut**), then **Install**.
3. Open it from its icon from now on.

**Notifications:**
1. Open **Setup**, tap **Turn on notifications**, then **Allow**.
2. If the test doesn't arrive, open your phone's **Settings → Apps**, find **Chrome** (or **Job Search HQ**, if listed), tap **Notifications**, and turn them on.
3. **Do not disturb** hides notifications. Swipe down from the top of the screen to check.
4. **Battery savers** on some phones stop notifications from arriving on time. If they arrive late, set Chrome's battery use to **Unrestricted** in **Settings → Apps → Chrome → Battery**.

**Microphone:** tap **Allow** (or **While using the app**) the first time you record an answer.

## Other browsers

- **Firefox:** the website, sign-in, notifications and the microphone all work in a Firefox tab. On Windows, Firefox 143 or later can also pin it to the taskbar as an app. On a Mac, use Chrome or Safari to install it as an app.
- **Older browsers,** and private or incognito windows, may not keep you signed in or show notifications. Use an up-to-date browser in a normal window.

## Using it on several devices

Sign in and turn on notifications on each device; each one is set up separately. Your records are the same everywhere, because they live on your server, not on the device.

**Lost a device?** On any other device, open **Settings** and choose **Sign out everywhere**. Then sign in again on the devices you still have.

---

# Part 3: Device problems

| What you see | Device | What to do |
|---|---|---|
| No **Install** icon in the address bar | Windows, Mac, Chromebook | Use the menu: **⋮ → Cast, save, and share → Install page as app**. If it's not there, you may already have it installed (look in your apps), or you're in a private window. |
| **Add to Home Screen** is missing | iPhone, iPad | You're probably not in Safari, or the Share sheet needs scrolling. Open the address in Safari and scroll down the Share sheet. |
| Notifications never arrive | Any | On Setup, choose **Send a test**. Then check the device's own notification settings and Do Not Disturb or Focus, as above for your device. |
| Notifications stopped after working | Any | Choose **Turn on notifications** on Setup again. Browser updates and clearing browsing data can reset them. |
| Notifications on iPhone don't appear | iPhone, iPad | Open the app from the home screen icon, not Safari, and turn them on there. iOS 16.4 or later is needed. |
| The microphone doesn't record | Any | Allow it in the browser, then in the device's privacy settings, as above for your device. Check no other app, such as a video call, is using it. |
| "This copy belongs to someone else" | Any | The browser is signed in to a different Google account. Use a separate Chrome profile, or sign out of Google and sign in with the right account. |
| Signed out every time I open it | Any | Don't use a private or incognito window, and don't set the browser to clear cookies on exit for this site. |
| Cloud Shell says it's blocked by an administrator | Work or school computer | Use a personal computer and a personal Google account. |
| Cloud Shell won't load, or keeps reconnecting | Any | Use Chrome in a normal window, allow cookies, and turn off ad blockers for `shell.cloud.google.com`. |
| Pasting into Cloud Shell does nothing | Any | Click once in the text window first, then paste again, and choose **Allow** if the browser asks to allow pasting. |
| I pressed Ctrl + C and the setup stopped | Windows, Chromebook | Nothing's lost. Run the setup again: [If Cloud Shell closes or stops](#if-cloud-shell-closes-or-stops). |
| The page is too small to read | Any | Zoom with **Ctrl + plus** (Windows, Chromebook) or **⌘ Command + plus** (Mac), or pinch on a phone. |

For problems that aren't about your device, see [install.md](install.md#troubleshooting) (setup) and [user-guide.md](user-guide.md#13-if-something-goes-wrong) (using the app).
