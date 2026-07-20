import yfinance as yf
import pandas as pd
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ==========================================
# 1. LIST YOUR 15 STOCKS HERE
# ==========================================
# Use standard tickers for US stocks, and add ".TO" for Toronto Stock Exchange stocks.
WATCHLIST = [
    "MSTR", "SPOT", "AI", "STLA", "IDCC", "SMCI", "PLTR", "NVDA", "TSLA",    # US Stocks (NYSE/NASDAQ)
    "SHOP.TO", "BCE.TO" # Canadian Stocks (TSX)
    # Add your remaining tickers here to reach 15...
]

def generate_action_notes(ticker, current_price, de_ratio, peg_ratio, roe_list, above_sma, rsi):
    """
    Automated logic engine that scans the metrics and writes personalized
    investment recommendations dynamically based on your rules.
    """
    score = 0
    notes = []
    
    # Evaluate Debt-to-Equity
    if isinstance(de_ratio, (int, float)):
        if de_ratio < 1.5:
            score += 1
        else:
            notes.append("High debt burden limits financial flexibility.")
            
    # Evaluate PEG Ratio
    if isinstance(peg_ratio, (int, float)):
        if peg_ratio < 1.0:
            score += 1
        elif peg_ratio > 2.0:
            notes.append("The stock is quite expensive relative to its expected growth.")
            
    # Evaluate ROE Trend
    try:
        clean_roes = [float(r.replace('%','')) for r in roe_list if r != 'N/A']
        if clean_roes and clean_roes[0] > 15:
            score += 1
        if len(clean_roes) >= 2 and clean_roes[0] < clean_roes[1]:
            notes.append("Warning: Profit efficiency (ROE) is trending downward.")
    except:
        pass

    # Evaluate Technical Indicators
    if above_sma == "YES":
        score += 1
    else:
        notes.append("Stock is locked in a macro downtrend under its 200-day SMA.")
        
    if isinstance(rsi, (int, float)):
        if rsi < 35:
            score += 1
            notes.append("RSI shows extreme selling fatigue; prime territory for a value bounce.")
        elif rsi > 70:
            notes.append("RSI is flashing heavily overbought. Avoid chasing or lock in partial profits.")

    # Determine Ultimate Verdict Based on Your Scoring System
    if score >= 4:
        verdict = f"🟢 VERDICT: STRONG BUY ({score}/5 Ticks)"
        notes.insert(0, f"Excellent fundamentals matching momentum. Look to add exposure.")
    elif score == 3:
        verdict = f"🟡 VERDICT: NEUTRAL / HOLD ({score}/5 Ticks)"
        notes.insert(0, "Decent health, but lacks strong technical or valuation triggers right now.")
    else:
        verdict = f"🔴 VERDICT: AVOID OR SELL ({score}/5 Ticks)"
        if not notes:
            notes.insert(0, "Weak fundamental scores or poor macro trends across several categories.")

    # Format notes list cleanly
    notes_str = "\n".join([f"     - {note}" for note in notes]) if notes else "     - No critical flags triggered."
    return f"  • {verdict}\n  • Action Notes:\n{notes_str}"


def analyze_stock(ticker_symbol):
    try:
        stock = yf.Ticker(ticker_symbol)
        
        # 1. Fetch info parameters
        info = stock.info
        current_price = info.get("currentPrice", 0.0)
        currency = info.get("currency", "USD")
        peg_ratio = info.get("pegRatio", "N/A")
        
        # 2. Fetch financials
        balance_sheet = stock.balance_sheet
        income_stmt = stock.financials
        
        # 3. Calculate Debt-to-Equity
        total_debt = balance_sheet.loc['Total Debt'].iloc[0] if 'Total Debt' in balance_sheet.index else 0
        total_equity = balance_sheet.loc['Stockholders Equity'].iloc[0] if 'Stockholders Equity' in balance_sheet.index else 1
        de_ratio = round(total_debt / total_equity, 2) if total_equity != 0 else "N/A"
        
        # 4. Calculate 3-Year ROE Trend
        roe_list = []
        for i in range(min(3, len(income_stmt.columns))):
            try:
                net_income = income_stmt.loc['Net Income'].iloc[i]
                equity = balance_sheet.loc['Stockholders Equity'].iloc[i]
                if equity and equity != 0:
                    roe_list.append(f"{round((net_income / equity) * 100, 1)}%")
                else:
                    roe_list.append("N/A")
            except:
                roe_list.append("N/A")
                
        # 5. Fetch 1-year historical prices (for SMA & RSI)
        history = stock.history(period="1y")
        if len(history) < 200:
            return f"❌ {ticker_symbol}: Not enough historical data to analyze.\n\n"
            
        last_close = history['Close'].iloc[-1]
        sma_200 = history['Close'].rolling(window=200).mean().iloc[-1]
        above_sma = "YES" if last_close > sma_200 else "NO"
        
        # 6. Calculate 14-day RSI
        delta = history['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = round(100 - (100 / (1 + rs.iloc[-1])), 1) if not pd.isna(rs.iloc[-1]) else "N/A"
        
        # 7. Generate the new automated Notes Section
        notes_section = generate_action_notes(ticker_symbol, current_price, de_ratio, peg_ratio, roe_list, above_sma, rsi)
        
        # Format comprehensive block
        report = (
            f"■ {ticker_symbol} ({currency})\n"
            f"  • Price: {current_price:.2f} {currency}\n"
            f"  • Debt-to-Equity: {de_ratio}\n"
            f"  • PEG Ratio: {peg_ratio}\n"
            f"  • 3-Year ROE Trend: {roe_list}\n"
            f"  • Above 200-day SMA: {above_sma} (SMA: {sma_200:.2f})\n"
            f"  • Current RSI: {rsi}\n"
            f"{notes_section}\n\n"
        )
        return report

    except Exception as e:
        return f"❌ Error analyzing {ticker_symbol}: {str(e)}\n\n"


def send_email(subject, body):
    # ==========================================
    # 2. CONFIGURE YOUR EMAIL SETTINGS HERE
    # ==========================================
    sender_email = "tridib.perfect@gmail.com"       
    sender_password = "tmlm gbsi ktpm mhmv"  # Stick your Google App Password code here 
    receiver_email = "tridib.perfect@gmail.com"     
    
    smtp_server = "smtp.gmail.com"
    smtp_port = 587
    
    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))
    
    try:
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, receiver_email, msg.as_string())
        server.quit()
        print("Email report sent with detailed metrics and action steps successfully!")
    except Exception as e:
        print(f"Failed to send email: {e}")

if __name__ == "__main__":
    print("Running portfolio intelligence engine...")
    full_report = "DAILY PORTFOLIO INTELLIGENCE & ACTION REPORT\n" + "="*46 + "\n\n"
    
    for ticker in WATCHLIST:
        print(f"Analyzing and scoring {ticker}...")
        full_report += analyze_stock(ticker)
        
    send_email("Daily Investment Strategy Report", full_report)