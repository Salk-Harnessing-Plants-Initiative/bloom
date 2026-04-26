const WORDS = [
  "Zero",
  "One",
  "Two",
  "Three",
  "Four",
  "Five",
  "Six",
  "Seven",
  "Eight",
  "Nine",
  "Ten",
  "Eleven",
  "Twelve",
  "Thirteen",
  "Fourteen",
  "Fifteen",
  "Sixteen",
  "Seventeen",
  "Eighteen",
  "Nineteen",
  "Twenty",
];

export function numberToWord(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "0";
  if (n >= 0 && n <= 20) return WORDS[n];
  return String(Math.floor(n));
}
