import scipy
import numpy as np


def extract_digits_Base10(n):
    #extract digits of euler's number
    digits = []
    eulerNumber = np.exp(1)
    for i in range(0,n):
        digits.append(np.floor(eulerNumber))
        eulerNumber = (eulerNumber - np.floor(eulerNumber)) * 10
    print(digits)

def approximate_euler(k):
    n = 10 ** (k+1)
    print( (1+1/n) ** n )

if __name__ == "__main__":
    
    eular = 0
    for i in range(0,100):
        eular += 1.0 / scipy.math.factorial(i)
    approximate_euler(10)
    print(eular)




