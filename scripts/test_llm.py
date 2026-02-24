from app.llm import call_llm_tex

system = "Ты полезный помощник."
user = "Верни LaTeX документ, который пишет: Привет мир."

out, model = call_llm_tex(system, user)
print("MODEL:", model)
print(out[:400])
