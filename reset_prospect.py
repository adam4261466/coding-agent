import sqlite3
c = sqlite3.connect('linkedin_intelligence/db/linkedin_intelligence.sqlite')

# Reset prospect state to MESSAGE_REVIEW so it can produce a new message and send
c.execute('UPDATE campaign_prospects SET status = "MESSAGE_REVIEW" WHERE campaign_id = "ai_engineers"')

# Clear all events for this prospect to give clean slate
c.execute('DELETE FROM events WHERE prospect_id = "p_11dd2f9c5d"')

c.commit()
c.close()
print("Prospect reset to MESSAGE_REVIEW, events cleared")