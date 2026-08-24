# Discord setup (Bogdan's task)

Enable Developer Mode: Settings -> Advanced -> Developer Mode ON (gives "Copy ID").

Create a private server (Add Server -> Create My Own). Inside it:
  - voice channel: `hotline`
  - text channel:  `hotline-log`

Two apps at https://discord.com/developers/applications -> New Application.
For BOTH: Bot tab -> **Public Bot: OFF**.

| setting                | hotline (archserver worker) | hotline-sentinel (Pigion) |
|------------------------|-----------------------------|---------------------------|
| Message Content Intent | ON                          | OFF                       |
| Server Members Intent  | OFF                         | OFF                       |
| Presence Intent        | OFF                         | OFF                       |
| OAuth2 scope           | bot                         | bot                       |
| permissions integer    | 36801536                    | 3072                      |

Invite URLs (replace client_id):
  https://discord.com/api/oauth2/authorize?client_id=APP1&permissions=36801536&scope=bot
  https://discord.com/api/oauth2/authorize?client_id=APP2&permissions=3072&scope=bot

36801536 = View Channels(1024) + Send Messages(2048) + Attach Files(32768)
         + Read Message History(65536) + Connect(1048576) + Speak(2097152)
         + Use Voice Activity(33554432)
3072     = View Channels + Send Messages

GUILD_VOICE_STATES (the intent that fires when you join a voice channel) is
NON-privileged - no portal toggle needed on either app.

Then fill in ~/data/hotline/.env (chmod 600):
  HOTLINE_BOT_TOKEN=
  SENTINEL_BOT_TOKEN=
  DISCORD_USER_ID=
  DISCORD_GUILD_ID=
  DISCORD_VOICE_CHANNEL_ID=
  DISCORD_TEXT_CHANNEL_ID=
