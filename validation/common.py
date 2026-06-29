class ValidationError:
    def __init__(self, location: str, message: str):
        self.location = location
        self.message = message

    def __str__(self):
        return f"[{self.location}] {self.message}"