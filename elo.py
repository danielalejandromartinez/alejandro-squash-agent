def calculate_elo(winner_elo, loser_elo, k_factor=32):
    """
    Calcula los nuevos puntajes después de un partido.
    
    winner_elo: Puntos actuales del ganador
    loser_elo: Puntos actuales del perdedor
    k_factor: Qué tan rápido cambian los puntos (32 es estándar)
    """
    
    # 1. Calcular la probabilidad de ganar
    # Si tienes más puntos, se espera que ganes (probabilidad alta)
    expected_winner = 1 / (1 + 10 ** ((loser_elo - winner_elo) / 400))
    
    # 2. Calcular cuántos puntos se mueven
    # Si ganaste y eras el favorito, ganas pocos puntos.
    # Si ganaste y eras el débil (sorpresa), ganas muchos puntos.
    change = k_factor * (1 - expected_winner)
    
    # 3. Redondear y aplicar los cambios
    points_gained = round(change)
    
    new_winner_elo = winner_elo + points_gained
    new_loser_elo = loser_elo - points_gained
    
    return new_winner_elo, new_loser_elo, points_gained