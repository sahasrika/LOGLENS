print("----------------TAIL RECURSION------------")
def function(n):
    if n==10:
        return
    function(n-1)
    print(n*2)
function(15)

print("----------------HEAD RECURSION------------")
def functions(x):
    if x==10:
        return
    print(x*2)
    functions(x-1)
functions(15)
