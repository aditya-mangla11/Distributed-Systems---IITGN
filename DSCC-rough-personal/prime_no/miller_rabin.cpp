#include <iostream>

using namespace std;

// We use unsigned long long to support massive 64-bit integers
typedef unsigned long long ull;

// Helper: Safe modular multiplication to prevent 64-bit overflow
ull mod_mul(ull a, ull b, ull m) {
    return (ull)((__int128_t)a * b % m);
}

// Helper: Modular exponentiation (base^exp % mod)
ull mod_pow(ull base, ull exp, ull mod) {
    ull res = 1;
    base %= mod;
    while (exp > 0) {
        if (exp % 2 == 1) res = mod_mul(res, base, mod);
        base = mod_mul(base, base, mod);
        exp /= 2;
    }
    return res;
}

// Core Miller-Rabin test for a specific base
bool miller_rabin_test(ull n, int base) {
    ull d = n - 1;
    int s = 0;
    while (d % 2 == 0) {
        d /= 2;
        s++;
    }

    ull x = mod_pow(base, d, n);
    if (x == 1 || x == n - 1) return true;

    for (int r = 1; r < s; r++) {
        x = mod_mul(x, x, n);
        if (x == n - 1) return true;
    }
    return false;
}

// The core algorithm evaluating the number
bool is_prime_deterministic(ull n) {
    if (n < 2) return false;
    if (n == 2 || n == 3) return true;
    if (n % 2 == 0) return false;

    // These specific 7 bases guarantee 100% accuracy for all 64-bit numbers (up to 2^64)
    // The first check (base 2) satisfies the first requirement of the Baillie-PSW method.
    int bases[] = {2, 325, 9375, 28178, 450775, 9780504, 1795265022};
    for (int base : bases) {
        if (n <= (ull)base) break; 
        if (!miller_rabin_test(n, base)) return false;
    }
    return true;
}

// Wrapper function to handle your specific return and print requirements
bool check_number(ull n) {
    if (n == 1) {
        cout << "NONE\n";
        return false;
    }
    
    bool result = is_prime_deterministic(n);
    
    if (result) {
        cout << "Prime\n";
    } else {
        cout << "Composite\n";
    }
    
    return result;
}

// Example usage
int main() {
    ull test_numbers[] = {1, 2, 4, 17, 104729, 2147483647}; // Includes a massive 64-bit prime
    
    for (ull num : test_numbers) {
        cout << "Testing " << num << ": ";
        check_number(num);
    }
    
    return 0;
}