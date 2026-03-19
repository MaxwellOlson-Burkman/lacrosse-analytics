import pandas as pd

# Load the data
data = pd.read_csv('data/processed/players/player_stats.csv')

# Sort 1: Team → Season → Roster
team_season = data.sort_values(by=['team_name', 'academic_year', 'name'])
team_season.to_csv('data/processed/players/player_stats_sorted_team_season.csv', index=False)

# Sort 2: Team → Player → Career
player_career = data.sort_values(by=['team_name', 'name', 'academic_year'])
player_career.to_csv('data/processed/players/player_stats_sorted_player_career.csv', index=False)

print("Both sorted files saved.")