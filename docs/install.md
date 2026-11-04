# Install Job Search HQ

This guide goes from nothing to signing in and chatting with your job search assistant. Every step works in a browser on a Windows PC, Mac or Chromebook.

**Before you start:**
- **New to this?** [start-here.md](start-here.md) explains what you need and every term used here, in plain language.
- **Your computer's keys and windows:** [devices.md, Part 1](devices.md#part-1-setting-up-from-your-computer) shows how to copy and paste into Cloud Shell, and how to put two windows side by side, on Windows, Mac and Chromebook.
- **The cheapest way to run it,** and when to start each free trial: [cheap.md](cheap.md).
- **Use a personal Google account and Chrome.** School and work Google accounts usually block Google Cloud and Gmail access.
- **Setting this up for someone else?** Get their Google email before you start; the setup asks for it about halfway through. If they use a Claude plan, have them beside you, or on the phone, for the last ten minutes: they sign in to their own Claude account at the end.
- **Allow about 90 minutes** the first time, including one wait of 10 to 25 minutes.

**What you'll end up with:** a small server on Google Cloud that you control. Each person you set it up for gets their own private copy at their own address, like `pat.jobs.example.com` on your own domain, or the free `pat.203-0-113-10.sslip.io`. They sign in with their Google account. Nobody else's data, including yours, is in their copy.

**Contents**
1. [Google Cloud account](#1-google-cloud-account)
2. [Claude](#2-claude)
3. [Run the installer](#3-run-the-installer)
4. [Set up Google sign-in](#4-set-up-google-sign-in)
5. [First sign-in](#5-first-sign-in)
6. [Set up a family member or friend](#6-set-up-a-family-member-or-friend)
7. [Your own domain (recommended)](#7-your-own-domain-recommended)
8. [Move to the free server](#8-move-to-the-free-server)
9. [Troubleshooting](#troubleshooting)

---

## 1. Google Cloud account

1. Go to [cloud.google.com/free](https://cloud.google.com/free) and sign in with your Google account.
2. Choose **Start free**.
   - New accounts get $300 of credit for 90 days.
   - A card is required. Google places a temporary hold to check it, not a charge.
3. Under **Billing**, set up a **budget alert**, for example $40 a month, so you get an email if something costs more than expected. (Billing → Budgets & alerts → Create budget.)

## 2. Claude

Choose one:
- **Claude Pro** ($20 a month): subscribe at [claude.ai](https://claude.ai) with your own account. You'll connect it at the end of the installer.
- **An Anthropic API key** (pay per use): create one at [console.anthropic.com](https://console.anthropic.com/settings/keys) and add $5 to $10 of credit. You'll paste it in the app's Settings.

## 3. Run the installer

1. Open **Cloud Shell**: [shell.cloud.google.com](https://shell.cloud.google.com). It's a terminal in your browser that's already signed in to Google Cloud. If a box called **Authorize Cloud Shell** appears at any point, choose **Authorize**.
2. Click once in it, paste this line and press Enter:
   ```
   git clone https://github.com/grantkovich/job-search-hq.git && cd job-search-hq && bash scripts/install.sh
   ```
   - **If Cloud Shell closes or the setup stops,** it's safe to run again: open Cloud Shell and paste `cd job-search-hq && git pull && bash scripts/install.sh`. Give the same answers as before. [devices.md](devices.md#if-cloud-shell-closes-or-stops) has the details.
   - **Your own copy of the code:** if it isn't on GitHub (a private fork, say), run it from that folder with `JOBHQ_REPO_URL=local bash scripts/install.sh`, and it uploads the folder instead of cloning.
3. Answer the questions. The setup has seven steps, numbered `1/7` to `7/7` in bold as it goes. Most questions are numbered menus: type the number and press Enter, or just press Enter for the choice in brackets. A few, like names, emails and the codes from Google, you type or paste. A wrong answer is explained and asked again.
   - **Project:** choose **Create a new project** (the suggested answer the first time) and press Enter to accept the suggested ID. When you run the setup again, the suggested answer is the `jobhq-…` project it made.
   - **Billing account:** pick the one with your trial credit. If you don't have one yet, it links you to start the trial and waits.
   - **Size:**
     - **1) Trial** (e2-medium): comfortable, with voice practice. The trial credit covers it.
     - **2) Forever free** (e2-micro): Google's always-free server. Smaller, with voice practice off. It needs an upgraded billing account (see [cheap.md](cheap.md)).
     - Most people start on trial and move to free before the trial ends (step 8).
   - **Region:** pick the one nearest you: us-east1 (South Carolina), us-central1 (Iowa) or us-west1 (Oregon). On the trial size, **Other region** lets you type another, like `europe-west2`; the setup checks that Google has it. The forever-free size only offers the three US regions.
4. **Wait a few minutes** while the script creates the server and installs Docker, the software that runs the app on it.
5. **The web address:** the script asks which base address to use. Each person's address is their name in front of it.
   - **Free:** an sslip.io address like `pat.203-0-113-10.sslip.io`, at your own risk (see step 7).
   - **Your own domain**, like `pat.jobs.example.com` (recommended): the script shows the one DNS record to add and checks it.

   Either way, each address gets a real HTTPS certificate.
6. **The first person:** the script says "Start with the person who will use it: you, or the person you're setting this up for." It asks for:
   - **a short name**, 2 to 21 lowercase letters, numbers or dashes, like `pat`. It becomes the start of their address.
   - **their Google account email.** Only this account can sign in.
   - **their time zone**, for reminders and the morning summary. The list has the US time zones; choose **Somewhere else** to type another, like `Europe/London`.

   Then it walks you through the Google sign-in setup one part at a time (step 4 below has the same steps), and asks you to paste the Client ID and the Client secret. It saves them on the server ("Saved."), then builds the app: "Building and starting pat's copy" takes **10 to 25 minutes** the first time, with nothing changing on screen. Don't press anything until it moves on to `7/7  Claude`.
7. **Claude:** finally, choose how this copy reaches Claude.
   - **Their own Claude plan:** it signs in to Claude Code on the server. Claude Code, Anthropic's own program, shows a link: open it, sign in to your own Claude account, and paste the code it gives you back into the terminal. Job Search HQ never asks for, reads or stores your Claude sign-in.
   - **An API key:** you paste it in the app's Settings later.

   It ends with "Done. Next:" and what to do: open the address, sign in with Google, and work down the Setup page.

**Your project ID and zone:** the setup's last line names both (`Server: jobhq (e2-medium) at … in us-east1-b`, and `?project=jobhq-…` in the cost link). The project ID is also the `jobhq-…` name in the project picker at the top of the Google Cloud console. Commands below use `us-east1-b` as an example; use your own zone.

The server is called `jobhq` and goes in zone `REGION-b` (for example `us-east1-b`). To use other names, set `JOBHQ_VM` or `JOBHQ_ZONE` before running the script.

The installer makes the server as safe as it can by default:
- **Network:** the server gets its own network. Only web traffic is open to the internet, and SSH only works through Google's secure tunnel.
- **No cloud permissions:** the server has none of its own.
- **Updates:** security updates install automatically.
- **Backups:** one is made every night.

**Finishing later instead:** answer the name question with Enter, and the setup stops there. To carry on, run the setup again (`cd job-search-hq && git pull && bash scripts/install.sh`) with the same project, size and region. Or do the rest by hand: connect with `gcloud compute ssh jobhq --zone YOUR-ZONE --tunnel-through-iap` (your zone is on the setup's last line), and run:
- `sudo jobsctl init`. It asks for the base address (press Enter to accept the free one), an optional email for certificate notices, then the Client ID and Client secret.
- `sudo jobsctl add-user NAME EMAIL --timezone ZONE`.
- `sudo jobsctl claude-login NAME`, to use a Claude plan.

## 4. Set up Google sign-in

This creates the "Sign in with Google" button and Gmail reading for your server. You do it once per server, in the same Google Cloud project. The installer shows these same steps one part at a time, with links that open each page in your project, and waits after each part. In the links below, replace `PROJECT` with your project ID.

**Part 1: register the app.** Open `https://console.cloud.google.com/auth/overview?project=PROJECT` and choose **Get started**. Fill in four screens, choosing **Next** after each:
- **App information:** App name `Job Search HQ`, and pick your email as the user support email.
- **Audience:** **External**.
- **Contact information:** your email.
- **Finish:** tick the box agreeing to the Google API Services: User Data Policy, then **Continue** and **Create**.

If you see an overview page with no **Get started** button, the app is already registered.

**Part 2: let it read Gmail.** Open `https://console.cloud.google.com/auth/scopes?project=PROJECT` and choose **Add or remove scopes**.
1. A panel opens. At its bottom, under **Manually add scopes**, paste these three lines:
   ```
   openid
   https://www.googleapis.com/auth/userinfo.email
   https://www.googleapis.com/auth/gmail.readonly
   ```
2. Choose **Add to table**, then **Update**.
3. Back on the page, choose **Save**.

**Part 3: say who can use it.** Open `https://console.cloud.google.com/auth/audience?project=PROJECT`. Leave **Publishing status** on **Testing**. Under **Test users**, choose **Add users**, paste the person's Google email, and choose **Save**. Up to 100 people can be on the list.
- **Don't skip this.** For anyone not on the list, **Connect Gmail** fails with "Access blocked: … has not completed the Google verification process". Sign-in still works for them, so it's easy to miss.
- **The cost of Testing:** Google ends Gmail access every 7 days. Job Search HQ notices, sends a notification and shows **Reconnect Gmail** on Today. Reconnecting takes a few seconds.
- **Why not publish:** a published app keeps Gmail connected. But before publishing, Google asks for full Branding details: a home page, a privacy policy and authorized domains, checked against Google Search Console. A free sslip.io address isn't yours to prove, so that route only works with your own domain (step 7), and this guide doesn't cover it.

**Part 4: create the sign-in client.** Open `https://console.cloud.google.com/auth/clients?project=PROJECT` and choose **Create client**.
1. Fill in the form:
   - **Application type:** Web application
   - **Name:** `Job Search HQ`
   - **Authorized JavaScript origins:** leave empty
   - **Authorized redirect URIs:** choose **Add URI** and paste the exact address the installer printed, like `https://pat.203-0-113-10.sslip.io/auth/callback`
2. Choose **Create**. A box shows the **Client ID** and **Client secret**.
3. Paste both into the installer. **Google shows the secret only this once**, so keep the box open (or choose **Download JSON**) until you have. Treat the secret like a password. Nothing appears on screen as you paste the secret; that's on purpose. Paste once, then press Enter. If the paste looks wrong (empty, pasted twice, or with a space in it), the setup says so and asks again.
4. **Then wait 10 to 25 minutes.** After "Saved.", the setup builds the app on the server. That's the long wait, and the screen doesn't change during it. Don't press anything until it moves on to `7/7  Claude`.

**Lost the secret?** If you closed Google's box before pasting it, or Cloud Shell closed: on the Clients page, open **Job Search HQ**, choose **Add secret**, copy the new secret, and run the setup again. The setup gives the same advice if the server couldn't save the sign-in settings.

**Adding another person later:** the installer and `jobsctl add-user` print both steps:
1. **Their address:** open the Clients page and choose **Job Search HQ**. Under **Authorized redirect URIs**, choose **Add URI**, paste their address (for example `https://sam.203-0-113-10.sslip.io/auth/callback`), and choose **Save**. Google says changes can take a few minutes to start working.
2. **Their Google account:** on the Audience page, add their email under **Test users**.

## 5. First sign-in

Open your address and choose **Sign in with Google**. Then work through the **Setup** page. Each item has its own instructions. For everything after setup, [user-guide.md](user-guide.md) explains each part of the app; send it to anyone you set up.

1. **Claude:** if you signed in to Claude Code during the install, open **Settings**, choose **My own Claude plan** and **Save**. With an API key, paste it in Settings and set a monthly budget; $10 to $25 is plenty for most people. Then choose **Test Claude**.
2. **Basic information and work history**, on the Profile page. Add each job, plus a few facts with numbers.
3. **Interview:** the AI asks you questions to fill in your profile. Approve what it suggests on the Suggestions page.
4. **Connect Gmail.**
   - Google shows "Google hasn't verified this app". That's expected for your own private app. Choose **Continue**. If you only see **Back to safety**, choose **Advanced**, then the **Go to … (unsafe)** link. Google names the app after your address's domain, for example sslip.io.
   - Then **tick the box** that allows reading email.
   - Job Search HQ can only read. It can't send, delete or label anything.
5. **Job alerts:** set up LinkedIn and Indeed alerts. The steps are on the Setup page.
6. **Notifications:** choose **Turn on notifications** on each device.
   - **Chromebook or computer:** first use **Install** in Chrome's address bar, so it opens like an app.
   - **iPhone:** in Safari, tap Share → **Add to Home Screen**, open it from the home screen, then turn on notifications.
7. **Free trials:** enter when your Google Cloud trial (and later your LinkedIn Premium trial) started, so you're reminded before anything bills.

## 6. Set up a family member or friend

You run the server, and they only use the website, for example from their Chromebook.

1. **Run the setup again** in Cloud Shell. It finds your server and adds the new person:
   ```
   cd job-search-hq && git pull && bash scripts/install.sh
   ```
   - Give the same project, size and region as the first time. It says "This server is already set up, with addresses under …".
   - When it asks for a short name and email, enter the new person's, like `sam` and `sam@example.com`.
   - It then shows the two Google steps for them and waits: add their address under **Authorized redirect URIs**, and add their email under **Test users**. It doesn't ask for the Client secret again.
   - Then it starts their copy. That's much quicker than the first time, because the app is already built.
   - Finally it asks how their copy reaches Claude. To use their own plan, have them with you: they sign in to their own Claude account.

   **Or, by command:** connect with `gcloud compute ssh jobhq --zone YOUR-ZONE --tunnel-through-iap`, then run `sudo jobsctl add-user sam sam@example.com --timezone America/New_York`. It prints the same two Google steps.
2. **Check both Google steps are done** (the redirect URI and the test user). Without the test user, their **Connect Gmail** fails with "Access blocked".
3. Send them their address. On first sign-in, walk them through the Setup page:
   - **Claude:** two options. Plans are personal, so never sign their copy in with yours.
     - **Their own plan:** run `sudo jobsctl claude-login sam` while they're with you. They open the link on their own device and sign in to their own Claude account.
     - **An API key:** theirs, or one you create in your own Anthropic account, with a budget in their Settings.
   - **Gmail:** they connect their own Gmail. Each copy only ever sees its own person's email.

Their copy is fully separate from yours:
- its own database and its own encryption key
- its own Google sign-in, which only works with their account
- its own network inside the server

To remove someone, run `sudo jobsctl remove-user sam`. It keeps a backup. Add `--delete-data` to also erase their data.

## 7. Your own domain (recommended)

**Which address to use:**
- **The free sslip.io address:** works with no setup, but it relies on sslip.io, a free outside service, to point your address at your server. Use it at your own risk. [security.md](security.md) explains what that service could and couldn't do.
- **Your own domain:** about $12 a year (usually $10 to $15) from any domain seller. It's what we recommend for a server you'll keep, especially one you run for other people.

**At install time:** the installer asks which you want. For your own domain, it shows the one DNS record to add, checks that it works, and sets everything up.

**To switch an existing server,** for example to `jobs.example.com`:

1. **DNS:** at your DNS provider, add an **A record** for `*.jobs` pointing at the server's IP address. The `*` covers every person. If your provider doesn't allow it, add one A record per person, like `pat.jobs`.
2. **Point the server at it:** run `sudo jobsctl init` and enter `jobs.example.com` as the base domain. It asks for the Google sign-in details again: press Enter to keep the Client ID, and paste the Client secret. If you no longer have it, make a new one with **Add secret** on the Clients page.
3. **Add people:** new people get the new domain. Existing people keep their old address until you change it: edit `JOBHQ_BASE_URL` in `/srv/jobhq/instances/NAME.env` to `https://NAME.jobs.example.com`, then run `sudo jobsctl update`. Their data stays as it is.
4. **Sign-in addresses:** add each new redirect URI to the Google sign-in client.

## 8. Move to the free server

Do this before the 90-day trial ends. Your Setup page reminds you 10 and 3 days before.

1. **Upgrade billing** to a paid account: [Billing → Overview](https://console.cloud.google.com/billing) → **Upgrade**. Without this, Google stops everything when the trial ends.
2. **Run the move:** in Cloud Shell, from the job-search-hq folder, run `bash scripts/move-to-free.sh` and answer its questions (the project, the server, and whether billing is upgraded). It takes about 35 minutes. It:
   - backs everyone up
   - replaces the server with an e2-micro, keeping the same IP address and everyone's addresses when you're in us-west1, us-central1 or us-east1
   - restores everyone, and keeps a copy of the old server's disk so you can go back

   From any other region, the new server is called `jobhq-free` and gets a new IP address. With free sslip.io addresses, everyone's address changes, and the script prints the new redirect URIs to add to the Google sign-in client. With your own domain, addresses stay the same, and you change the one DNS record to the new IP.

   If something goes wrong, the script stops and prints where your backup is and how to try again. [operations.md](operations.md#undo-a-move) explains how to go back to the old server.
3. **Check, then clean up:** open each address, check the data, then delete the old disk's copy with the command the script prints.

## Troubleshooting

**Commands that start with `sudo jobsctl` run on the server, not in Cloud Shell itself.** In Cloud Shell, connect first with `gcloud compute ssh jobhq --zone YOUR-ZONE --tunnel-through-iap` (your zone is on the setup's last line), then type the command. Type `exit` to leave the server.

| What you see | What to do |
|---|---|
| The browser says the site can't be reached | Check the address matches the one the installer printed. With your own domain, DNS may not have updated yet: wait, then try `dig +short NAME.jobs.example.com`. |
| "Your connection is not private" just after setup | Caddy is still getting the certificate. Wait a minute and reload. If it persists, check `sudo jobsctl logs NAME` and that ports 80 and 443 are open (the `jobhq-net-web` firewall rule). |
| Google says `redirect_uri_mismatch` | The redirect URI in step 4, Part 4, doesn't exactly match `https://NAME.<your address>/auth/callback`. Fix it under Clients. |
| "This copy of Job Search HQ belongs to someone else" | You signed in with a different Google account than the one given to `add-user`. |
| "The server couldn't save the sign-in settings" | Read the lines above it for why. Nothing is lost: run the setup again. If Google's box with the Client secret is closed, make a new secret first with **Add secret** (step 4, Part 4). |
| "Adding NAME didn't finish" | Read the lines above it for why, then run the setup again with the same answers. If it says the person "already has a copy on this server", there's nothing to add. |
| "That looks like the secret pasted twice" or "That has a space in it" | Copy only the Client secret from Google's box, and paste it once. |
| "The Claude sign-in didn't finish" | Do it later, as the message says: connect to the server, run `sudo jobsctl claude-login NAME`, then choose **My own Claude plan** in Settings and **Save**. |
| Connect Gmail says "Access blocked: … has not completed the Google verification process" | That Google account isn't on the Test users list. Add it (step 4, Part 3), then connect Gmail again. |
| Gmail disconnects every week | Expected while the app is in Testing. Choose **Reconnect Gmail** on Today, or the notification. |
| Settings says Claude Code isn't signed in | Run `sudo jobsctl claude-login NAME` on the server again, then Save in Settings. |
| "Claude rejected the sign-in" | With an API key: it was mistyped, revoked, or the Anthropic account has no credit; paste a new one in Settings. With a Claude plan: run `sudo jobsctl claude-login NAME` again. |
| Voice answers say they're turned off | Voice practice is off on the free server, or the copy was added with `--no-voice`. On a trial-size server, edit `/srv/jobhq/instances/NAME.env`, set `JOBHQ_VOICE=on`, then run `sudo jobsctl update`. |
| `fatal: destination path 'job-search-hq' already exists` | You've run the setup before. Paste `cd job-search-hq && git pull && bash scripts/install.sh` instead. |
| A Google Cloud console page says "You need additional access" or shows no project | You're signed in to the console with a different Google account. Switch accounts with the picture at the top right. |
| The setup offers "Create a new project" as the suggested answer when you've run it before | You may be signed in with a different Google account. If your `jobhq-…` project is in the list, type its number instead, so it carries on with the same server. |
| Anything else | Run `sudo jobsctl status` and `sudo jobsctl logs NAME`. See [operations.md](operations.md). |
