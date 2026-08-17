# Deck ingestion contract

deck identity は official local card data を正典にした Card ID の順序非依存 60-card multiset digest です。`EXACT_60_VALID`、`CARD_ID_UNRESOLVED`、`INVALID_COUNT` を区別し、名称推測でカードを補いません。同一 digest は統合し、source provenance は維持します。
