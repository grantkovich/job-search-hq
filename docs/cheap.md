# An AI job search aid for about $20 a month, plus a domain

This guide sets Job Search HQ up as cheaply as possible:
- a Google Cloud free trial for the server
- a $20 Claude Pro plan for the AI
- your own domain for the web address, about $12 a year, or a free sslip.io address to try it first
- an optional LinkedIn Premium free trial for reaching people

Prices and trial terms below were checked on November 3, 2026. They change, so follow the links before you rely on them.

## What it costs

| | First 3 months | After that |
|---|---|---|
| Server | **$0.** Google Cloud's free trial gives new accounts $300 of credit for 90 days, which covers the trial-size server (about $25 a month) | **About $0 to $5.** Google's always-free e2-micro server, plus possible small charges such as the server's public IP address |
| AI | **$20.** Claude Pro | **$20**, or cancel once you're hired |
| LinkedIn Premium Career | **$0** for the trial month (optional) | About $30 to $40 a month if you keep it. Optional. |
| Web address | **About $1 a month.** Your own domain costs about $10 to $15 a year, and we recommend it for a real search ([install.md, step 7](install.md#7-your-own-domain-recommended)). To try Job Search HQ first, use the free `NAME.<ip>.sslip.io` address for **$0**. It has a real HTTPS certificate but depends on a free outside service, so use it at your own risk ([security.md](security.md#known-limits)). | Same |
| **Total** | **About $21 a month**, or $20 on the free address | **About $21 to $26 a month** |

**Instead of Claude Pro:** you can use an Anthropic API key and pay per use, with a monthly cap you set. Light use often costs a few dollars a month.

## The order that gets the most out of each trial

**Day 1: Google Cloud trial and Claude Pro**
1. **Start the Google Cloud free trial** at [cloud.google.com/free](https://cloud.google.com/free).
   - It needs a card. Google places a temporary hold to check it, not a charge.
   - You're not billed for usage the credit covers.
2. **Subscribe to Claude Pro** at [claude.ai](https://claude.ai). Use your own account; plans are personal.
3. **Run the installer** from Cloud Shell, following [install.md](install.md):
   - Pick size **1) trial**.
   - It creates the server, asks which web address to use (the free sslip.io one, or your own domain), and walks you through the Google sign-in client.
   - At the end it offers to sign you in to Claude Code with your Claude plan.
   - That sign-in happens in Claude Code's own screen. Job Search HQ never asks for, reads or stores it: Claude Code keeps it in that person's own folder on your server.
4. **Record the trial:** on the app's **Setup** page, under **Free trials**, enter today as the Google Cloud trial start. You'll get reminders 10 and 3 days before it ends.

**Week 1: build your profile, for free**
- **Profile:** fill in your basics and work history, then let the AI interview you.
- **Job alerts:** set up LinkedIn and Indeed alerts and connect Gmail. Job alerts are free; you don't need Premium for them.
- **Targets:** pick roles and add the people at those companies you'd like to reach, on each role's **People** section.

**When you're ready to reach out: the LinkedIn Premium trial**
- **Start it once your profile is built and your targets are picked,** so the month counts. ([LinkedIn's trial page](https://www.linkedin.com/help/learning/answer/72))
- **The trial's terms:**
  - It's usually one month.
  - It needs a card and renews into a paid plan unless you cancel.
  - You can't start another free trial for 12 months.
- **What it's good for here:**
  - InMail credits, to message people you're not connected to.
  - Insights on how you compare with other applicants.
- **Drafting:** Job Search HQ's **Outreach** drafts InMails and connection notes from your real experience. You copy them into LinkedIn and mark them sent, and it reminds you to follow up. It never connects to LinkedIn itself.
- **Record the trial:** enter its start date under **Free trials**. You'll get reminders 5 days and 1 day before it renews.

**Around day 80: move to the free server**
1. **Upgrade billing:** in the Google Cloud console, go to **Billing → Overview → Upgrade**. When the trial ends without an upgrade, Google stops everything and deletes it after a 30-day grace period. The always-free server needs an upgraded, paid billing account. ([Google's free tier and trial terms](https://docs.cloud.google.com/free))
2. **Run the move:** in Cloud Shell, from the Job Search HQ folder, run `bash scripts/move-to-free.sh`. It takes about 35 minutes. It:
   - backs everyone up
   - replaces the server with Google's free e2-micro, keeping the same address when you're in us-west1, us-central1 or us-east1
   - restores everyone
   - keeps a copy of the old server's disk until you've checked
3. **Check, then clean up:** open your address, check your data, then delete the old disk's copy as the script tells you. [install.md, step 8](install.md#8-move-to-the-free-server) covers moves from other regions.

**The free server's limits:**
- **Size:** one e2-micro with 1 GB of memory, in us-west1, us-central1 or us-east1, with up to 30 GB of standard disk and 1 GB of outbound data a month.
- **Voice practice is off on it.** Typed practice still works.
- **Expect AI tasks to queue:** they run one at a time there. Page loads and PDFs are still quick (measured numbers are in the next section).

**When you're hired**
- **Download your data:** Settings → Download my data.
- **Cancel** Claude Pro and any LinkedIn plan.
- **Shut the server down:** delete the Google Cloud project (console → IAM & Admin → Settings → Shut down).

## Measured on a real free server

Measured on an e2-micro made by `move-to-free.sh` on November 3, 2026, with two people's copies:

| What | Measured |
|---|---|
| Memory with two people's copies, idle | About 80 MB per copy, 24 MB for the web server, about 700 MB of the 1 GB used in total, with a little swap |
| Building a two-page resume PDF | 1.4 seconds |
| Starting a Claude task | About 8 seconds before Claude starts answering; that copy peaked at 300 MB, inside its 700 MB limit |
| Opening a page | 120 to 160 ms over HTTPS |
| First start, building the app on the server | 10 to 25 minutes, depending on the server size. With a prebuilt image (`JOBHQ_IMAGE`, see operations.md), it's a download instead |
| `move-to-free.sh`, start to finish, two people | About 35 minutes, most of it that first build |

The free server handles one or two people's job searches comfortably. AI tasks run one at a time there, so a second request waits for the first.

## Fine print

- **Claude plans:** Anthropic's terms allow you to sign in to the unmodified Claude Code with your own plan, which is what `jobsctl claude-login` does. They don't allow an app to collect or store your Claude.ai credentials, which is why Job Search HQ never asks for them. ([Claude Code legal and compliance](https://code.claude.com/docs/en/legal-and-compliance))
- **Plan usage:** your Claude Pro usage is shared with your other Claude use, and Pro's limits assume ordinary individual use. A busy day of drafting can hit them, and the app then waits until your limit resets.
- **Hosting someone else:** you can host a family member's copy on your server. They either sign in to Claude Code with their own plan, or use an API key, theirs or one you pay for. Never sign their copy in with your plan.
- **Google's warning:** Google shows a "Google hasn't verified this app" screen when you connect Gmail. That's your own private app, kept in Google's "Testing" mode for up to 100 people you list.
- **Weekly Gmail reconnect:** in Testing mode, Google ends Gmail access every 7 days. The app notifies you, and reconnecting is one tap. Avoiding it means publishing the app, which needs your own domain and Google's review of Gmail access. This guide doesn't cover that.
- **Names:** Job Search HQ isn't made, endorsed or supported by Anthropic, Google or LinkedIn.
