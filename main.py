import discord
from discord.ext import commands
import sqlite3
import random
import os
import asyncio
import time
from dotenv import load_dotenv
from keep_alive import keep_alive

# 環境変数をロード
load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# データベースのセットアップ
conn = sqlite3.connect('levels.db')
c = conn.cursor()
c.execute('''
    CREATE TABLE IF NOT EXISTS levels
    (user_id INTEGER PRIMARY KEY, xp INTEGER, level INTEGER, coins INTEGER, last_work_time INTEGER)
''')

# last_work_timeカラムが存在しない場合に追加
try:
    c.execute('ALTER TABLE levels ADD COLUMN last_work_time INTEGER')
except sqlite3.OperationalError:
    pass

conn.commit()

# 経験値とレベルアップの計算
def calculate_xp(level):
    return 100 + (level - 1) * 20

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    user_id = message.author.id
    c.execute('SELECT * FROM levels WHERE user_id=?', (user_id,))
    result = c.fetchone()

    if result is None:
        c.execute('INSERT INTO levels (user_id, xp, level, coins, last_work_time) VALUES (?, ?, ?, ?, ?)', 
                  (user_id, 0, 1, 0, 0))
        conn.commit()
        result = (user_id, 0, 1, 0, 0)

    xp, level, coins = result[1], result[2], result[3]
    xp += random.randint(1, 10)
    required_xp = calculate_xp(level)

    if xp >= required_xp:
        xp -= required_xp
        level += 1
        coins += 10

    c.execute('UPDATE levels SET xp=?, level=?, coins=? WHERE user_id=?', 
              (xp, level, coins, user_id))
    conn.commit()
    await bot.process_commands(message)

@bot.command()
async def rank(ctx, member: discord.Member = None):
    if member is None:
        member = ctx.author

    user_id = member.id
    c.execute('SELECT * FROM levels WHERE user_id=?', (user_id,))
    result = c.fetchone()

    if result is None:
        await ctx.send(f'{member.display_name}さんはまだレベルがありません。')
        return

    xp, level, coins = result[1], result[2], result[3]
    await ctx.send(f'{member.display_name}さんのランクカード:\nレベル: {level}\n経験値: {xp}\nナオコイン: {coins}')

# 高い低いゲーム
@bot.command()
async def highlow(ctx, bet: int):
    if bet <= 0:
        await ctx.send("無効な掛け金です。正の整数を入力してください。")
        return

    user_id = ctx.author.id
    c.execute('SELECT coins FROM levels WHERE user_id=?', (user_id,))
    result = c.fetchone()

    if result is None or result[0] < bet:
        await ctx.send("ナオコインが不足しています。")
        return

    current_number = random.randint(1, 100)
    streak = 0
    lost = False

    while streak < 5:
        next_number = random.randint(1, 100)

        if random.random() < 0.75:
            if current_number < 50:
                next_number = random.randint(current_number + 1, 100)
            else:
                next_number = random.randint(1, current_number - 1)

        await ctx.send(f"現在の数字: {current_number}\n次の数字は高い？低い？ (h/l)")

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content.lower() in ['h', 'l']

        try:
            msg = await bot.wait_for('message', check=check, timeout=30.0)
        except asyncio.TimeoutError:
            await ctx.send(f"時間切れです。{streak}回連続正解でした。")
            break

        guess = msg.content.lower()
        if (guess == 'h' and next_number > current_number) or (guess == 'l' and next_number < current_number):
            streak += 1
            await ctx.send(f"正解！次の数字は{next_number}でした。連続{streak}回正解！")
            current_number = next_number
        else:
            lost = True
            await ctx.send(f"不正解。次の数字は{next_number}でした。{streak}回連続正解でした。")
            break

    if lost:
        c.execute('UPDATE levels SET coins = coins - ? WHERE user_id = ?', (bet, user_id))
        await ctx.send(f"{ctx.author.mention}さん、ゲームに負けました。掛け金{bet}ナオコインが減らされました。")
    else:
        winnings = bet * (2 ** streak)
        c.execute('UPDATE levels SET coins = coins + ? WHERE user_id = ?', (winnings - bet, user_id))
        await ctx.send(f"{ctx.author.mention}さんのナオコインが{winnings}増えました！")

    conn.commit()

# 新しい!workコマンド
@bot.command()
async def work(ctx):
    user_id = ctx.author.id
    current_time = int(time.time())
    c.execute('SELECT last_work_time FROM levels WHERE user_id = ?', (user_id,))
    last_work_time = c.fetchone()[0]

    if last_work_time is not None and current_time - last_work_time < 7200:
        remaining_time = 7200 - (current_time - last_work_time)
        hours, remainder = divmod(remaining_time, 3600)
        minutes, seconds = divmod(remainder, 60)
        await ctx.send(f"{ctx.author.mention}さん、まだクールタイム中です。次に働けるまで {hours}時間{minutes}分{seconds}秒 残っています。")
        return

    c.execute('UPDATE levels SET coins = coins + 5, last_work_time = ? WHERE user_id = ?', (current_time, user_id))
    conn.commit()
    await ctx.send(f"{ctx.author.mention}さん、お仕事お疲れ様です！5ナオコインを獲得しました。")

# ナオコインを他のユーザーに渡すコマンド
@bot.command()
async def give(ctx, member: discord.Member, amount: int):
    if amount <= 0:
        await ctx.send("無効な送金額です。正の整数を入力してください。")
        return

    giver_id = ctx.author.id
    receiver_id = member.id

    c.execute('SELECT coins FROM levels WHERE user_id = ?', (giver_id,))
    giver_coins = c.fetchone()[0]

    if giver_coins < amount:
        await ctx.send(f"{ctx.author.mention}さん、ナオコインが不足しています。")
        return

    c.execute('UPDATE levels SET coins = coins - ? WHERE user_id = ?', (amount, giver_id))
    c.execute('UPDATE levels SET coins = coins + ? WHERE user_id = ?', (amount, receiver_id))
    conn.commit()

    await ctx.send(f"{ctx.author.mention}さんが{member.mention}さんに{amount}ナオコインを送金しました。")

# keep_alive関数を呼び出し
keep_alive()

# トークンの取得
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    print("エラー: DISCORD_TOKENが設定されていないか、空です。")
    print("Replitのsecretsタブで'DISCORD_TOKEN'が正しく設定されているか確認してください。")
    exit(1)

try:
    bot.run(TOKEN)
except discord.errors.LoginFailure:
    print("エラー: 不正なトークンです。Discord開発者ポータルで正しいトークンを確認してください。")
except Exception as e:
    print(f"エラーが発生しました: {e}")




