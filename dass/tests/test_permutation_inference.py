from __future__ import annotations

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DASS_DIR = PROJECT_ROOT / "code" / "dass"
if str(DASS_DIR) not in sys.path:
    sys.path.insert(0, str(DASS_DIR))

from run.permutation_inference import (
    contiguous_block_permutation_indices,
    two_sided_permutation_p_value,
)


def _match_count(stat_left: list[float], stat_right: list[float]) -> float:
    return float(sum(1 for lhs, rhs in zip(stat_left, stat_right) if lhs == rhs))


class PermutationInferenceTests(unittest.TestCase):
    def test_contiguous_block_permutation_indices_reproducible(self) -> None:
        params = dict(n=12, block_length=3, n_permutations=7, seed=20260222)
        first = list(
            contiguous_block_permutation_indices(
                n=params["n"], block_length=params["block_length"], n_permutations=params["n_permutations"], seed=params["seed"]
            )
        )
        second = list(
            contiguous_block_permutation_indices(
                n=params["n"], block_length=params["block_length"], n_permutations=params["n_permutations"], seed=params["seed"]
            )
        )

        self.assertEqual(first, second)

    def test_contiguous_block_permutation_indices_valid_bounds_and_length(self) -> None:
        n = 10
        block_length = 4
        permutations = list(
            contiguous_block_permutation_indices(
                n=n,
                block_length=block_length,
                n_permutations=32,
                seed=17,
            )
        )

        expected_values = list(range(n))
        for permuted_indices in permutations:
            self.assertEqual(len(permuted_indices), n)
            self.assertEqual(sorted(permuted_indices), expected_values)
            self.assertTrue(all(0 <= idx < n for idx in permuted_indices))

            seen_block_ids: set[int] = set()
            start = 0
            while start < n:
                block_id = permuted_indices[start] // block_length
                self.assertLess(block_id, (n + block_length - 1) // block_length)
                self.assertNotIn(block_id, seen_block_ids)
                seen_block_ids.add(block_id)

                expected = block_id * block_length
                end = start
                while end < n and permuted_indices[end] // block_length == block_id:
                    self.assertEqual(
                        permuted_indices[end],
                        expected + (end - start),
                    )
                    end += 1

                self.assertLessEqual(end - start, block_length)
                start = end

    def test_two_sided_p_value_reproducible_with_seed(self) -> None:
        left = [0.0, 1.0, 0.0, 1.0]
        right = [0.0, 1.0, 0.0, 1.0]

        first = two_sided_permutation_p_value(
            left,
            right,
            statistic=_match_count,
            block_length=2,
            n_permutations=128,
            seed=99,
        )
        second = two_sided_permutation_p_value(
            left,
            right,
            statistic=_match_count,
            block_length=2,
            n_permutations=128,
            seed=99,
        )

        self.assertEqual(first, second)

    def test_two_sided_p_value_monotonicity_basics(self) -> None:
        left = [0.0, 1.0, 0.0, 1.0, 0.0, 1.0]
        strong = [0.0, 1.0, 0.0, 1.0, 0.0, 1.0]
        moderate = [0.0, 0.0, 1.0, 1.0, 0.0, 1.0]
        weak = [0.0, 1.0, 1.0, 0.0, 1.0, 0.0]

        p_strong = two_sided_permutation_p_value(
            left,
            strong,
            statistic=_match_count,
            block_length=1,
            n_permutations=200,
            seed=20260222,
        )
        p_moderate = two_sided_permutation_p_value(
            left,
            moderate,
            statistic=_match_count,
            block_length=1,
            n_permutations=200,
            seed=20260222,
        )
        p_weak = two_sided_permutation_p_value(
            left,
            weak,
            statistic=_match_count,
            block_length=1,
            n_permutations=200,
            seed=20260222,
        )

        self.assertLess(p_strong, p_moderate)
        self.assertLess(p_moderate, p_weak)


if __name__ == "__main__":
    unittest.main()
